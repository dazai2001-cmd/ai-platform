import json
import math
import numbers
import re
import sqlite3
import threading
import time
import zipfile
from contextlib import contextmanager
from io import BytesIO
import pandas as pd
from pathlib import Path
from infrastructure.llm.ollama_client import ollama
from core.config.settings import settings
from services.bi.dataset_repository import dataset_repository

_PROMPT = (Path(__file__).parents[2] / "core/prompts/bi_prompt.txt").read_text()
_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_DANGEROUS_SQL = re.compile(
    r"\b(attach|alter|create|delete|detach|drop|insert|pragma|replace|update|vacuum)\b",
    re.IGNORECASE,
)
_FORBIDDEN_QUERY_SHAPES = re.compile(
    r"\b(join|union|intersect|except|with|recursive)\b",
    re.IGNORECASE,
)
_SQL_COMMENT = re.compile(r"--|/\*|\*/")
_SQL_FUNCTION_CALL = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", re.IGNORECASE)
_ALLOWED_SQL_FUNCTIONS = frozenset({
    "abs", "avg", "ceil", "ceiling", "coalesce", "count", "date", "datetime",
    "floor", "ifnull", "instr", "length", "like", "lower", "ltrim", "max", "min",
    "nullif", "pow", "power", "round", "rtrim", "sqrt", "strftime", "substr",
    "substring", "sum", "time", "total", "trim", "typeof", "upper",
})
_SQL_PAREN_KEYWORDS = frozenset({"cast", "in"})
_MAX_RESULT_ROWS = 200
_SQL_PROGRESS_OPCODES = 1000

# Compatibility-only in-memory store for legacy fixtures. Durable uploads are
# parsed on demand so public users cannot fill a Render worker's RAM via cache.
_datasets: dict[str, pd.DataFrame] = {}
_dataset_versions: dict[str, float] = {}
_manifest = Path(settings.BI_MANIFEST_PATH)
_MANIFEST_LOCK = threading.RLock()
_BI_QUERY_SLOTS = threading.BoundedSemaphore(settings.BI_MAX_CONCURRENT_QUERIES)


class BIInputError(ValueError):
    """A safe, user-correctable BI request error."""


class BIProviderError(RuntimeError):
    """A safe, user-facing failure returned by the configured BI model provider."""

    def __init__(self, message: str):
        rate_limited = "429" in message or "rate limit" in message.lower()
        self.status_code = 429 if rate_limited else 502
        safe_message = (
            "The BI model is temporarily rate-limited. Wait a moment and try again."
            if rate_limited
            else "The BI model is temporarily unavailable. Please try again."
        )
        super().__init__(safe_message)


class BIBusyError(BIProviderError):
    """Raised before loading a frame when this worker is at its BI memory limit."""

    def __init__(self):
        RuntimeError.__init__(
            self,
            "BI analysis is busy on this server. Wait a moment and try again.",
        )
        self.status_code = 503


class BIPipeline:
    def __init__(self):
        self._load_manifest()

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _dataset_key(self, name: str, user_id: str = "local") -> str:
        return f"{user_id}:{name}"

    def load_csv(self, path: str, name: str, user_id: str = "local") -> dict:
        with self._frame_slot():
            name = self._normalize_name(name)
            self._validate_upload_size(path)
            df = pd.read_csv(path, nrows=settings.MAX_DATASET_ROWS + 1)
            self._validate_dataframe(df)
            name = self._commit_dataset(name, path, "csv", df, user_id=user_id, unique=True)
            return {"name": name, "rows": len(df), "columns": list(df.columns)}

    def load_excel(self, path: str, name: str, user_id: str = "local") -> dict:
        with self._frame_slot():
            name = self._normalize_name(name)
            self._validate_upload_size(path)
            self._validate_excel_archive(path)
            df = pd.read_excel(path, nrows=settings.MAX_DATASET_ROWS + 1)
            self._validate_dataframe(df)
            name = self._commit_dataset(name, path, "excel", df, user_id=user_id, unique=True)
            return {"name": name, "rows": len(df), "columns": list(df.columns)}

    def list_datasets(self, user_id: str = "local") -> list[dict]:
        stored = dataset_repository.list_for_user(user_id)
        if stored:
            return stored
        # Compatibility for isolated unit fixtures and one-time legacy
        # manifest migration. Cloud requests use the durable repository.
        with _MANIFEST_LOCK:
            return [
                {"name": key.split(":", 1)[1], "rows": len(df), "columns": list(df.columns)}
                for key, df in _datasets.items()
                if key.startswith(f"{user_id}:") and key not in _dataset_versions
            ]

    def get_sample(self, name: str, user_id: str = "local") -> dict | None:
        with self._frame_slot():
            name = self._validate_name(name)
            df = self._get_dataset(name, user_id, nrows=5)
            if df is None:
                return None
            return {
                "columns": list(df.columns),
                "sample": df.head(5).to_dict(orient="records")
            }

    def delete_dataset(self, name: str, user_id: str = "local") -> bool:
        name = self._validate_name(name)
        key = self._dataset_key(name, user_id)
        with _MANIFEST_LOCK:
            records = self._read_manifest_unlocked()
            removed = [
                record for record in records
                if record.get("name") == name and record.get("user_id", "local") == user_id
            ]
            remaining = [record for record in records if record not in removed]
            durable_removed = dataset_repository.delete(user_id, name)
            if not removed and key not in _datasets and not durable_removed:
                return False
            if removed:
                self._write_manifest_unlocked(remaining)
            _datasets.pop(key, None)
            _dataset_versions.pop(key, None)
            self._remove_unreferenced_uploads(removed, remaining)
            return True

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def ask(
        self,
        question: str,
        dataset_name: str = None,
        model: str = None,
        user_id: str = "local",
        history: list[dict] | None = None,
    ) -> dict:
        with self._frame_slot():
            return self._ask_bounded(
                question,
                dataset_name=dataset_name,
                model=model,
                user_id=user_id,
                history=history,
            )

    @staticmethod
    @contextmanager
    def _frame_slot():
        """Bound every request path that materializes a full DataFrame."""
        acquired = _BI_QUERY_SLOTS.acquire(
            timeout=settings.BI_QUERY_SLOT_TIMEOUT_SECONDS
        )
        if not acquired:
            raise BIBusyError()
        try:
            yield
        finally:
            _BI_QUERY_SLOTS.release()

    def _ask_bounded(
        self,
        question: str,
        dataset_name: str = None,
        model: str = None,
        user_id: str = "local",
        history: list[dict] | None = None,
    ) -> dict:
        model = model or settings.TASK_MODELS["bi"]
        available = self.list_datasets(user_id)
        if not available:
            raise BIInputError("No dataset is available. Upload a CSV or Excel file first.")
        if not dataset_name:
            raise BIInputError("Select a dataset before asking a BI question.")
        display_name = self._validate_name(dataset_name)
        # Retain a stable DataFrame reference for the duration of this query;
        # another worker may replace the durable copy concurrently.
        df = self._get_dataset(display_name, user_id)
        if df is None:
            raise BIInputError(
                f"Dataset '{display_name}' was not found. Select an available dataset and try again."
            )
        schema = self._schema(df)
        question = (question or "").strip()
        if not question:
            raise BIInputError("A BI question is required.")
        if len(question) > min(4000, settings.MAX_PROMPT_CHARS // 2):
            raise BIInputError("BI question is too long.")

        prompt = _PROMPT.format(
            schema=schema,
            history=self._history_text(history),
            question=question,
        )
        if len(prompt) > settings.MAX_PROMPT_CHARS:
            raise BIInputError(
                "The dataset schema and question are too large to analyse safely. "
                "Use shorter column names or a shorter question."
            )
        try:
            response = ollama.generate(model, prompt, temperature=0.1)
        except RuntimeError as exc:
            raise BIProviderError(str(exc)) from exc

        chart_hint = self._extract_chart(response)

        # Try to run SQL if present
        sql = self._extract_sql(response)
        rows = []
        chart = None
        answer = self._clean_answer(response)
        if sql:
            try:
                sql = re.sub(r"\byour_table_name\b", "dataset", sql, flags=re.IGNORECASE)
                sql = self._normalize_sqlite_sql(sql)
                sql = self._validate_select_sql(sql)
                result_df = self._execute_select(df, sql)
                rows = result_df.to_dict(orient="records")
                if chart_hint and rows:
                    chart = self._build_chart(rows, chart_hint, question)
                answer = self._result_answer(rows, chart is not None)
            except Exception as e:
                answer = f"I could not run the generated query: {e}"
                chart = None

        return {
            "answer": answer,
            "sql": sql,
            "rows": rows,
            "chart": chart,
            "model": model,
            "dataset": display_name
        }

    @staticmethod
    def _execute_select(df: pd.DataFrame, sql: str) -> pd.DataFrame:
        """Execute generated SQL in a locked-down, time-bounded SQLite sandbox."""
        connection = sqlite3.connect(":memory:")
        timed_out = False
        denied_function: str | None = None

        try:
            # Populate the only readable table before installing the read-only
            # authorizer. pandas handles nullable and timestamp column adapters.
            df.to_sql("dataset", connection, index=False, if_exists="replace")
            try:
                connection.enable_load_extension(False)
            except (AttributeError, sqlite3.DatabaseError):
                pass

            limits = (
                ("SQLITE_LIMIT_LENGTH", settings.BI_MAX_RESULT_CELL_BYTES),
                ("SQLITE_LIMIT_SQL_LENGTH", settings.BI_MAX_SQL_CHARS + 256),
                ("SQLITE_LIMIT_COLUMN", settings.MAX_DATASET_COLUMNS),
                ("SQLITE_LIMIT_EXPR_DEPTH", 100),
                ("SQLITE_LIMIT_COMPOUND_SELECT", 0),
                ("SQLITE_LIMIT_ATTACHED", 0),
            )
            if hasattr(connection, "setlimit"):
                for constant_name, value in limits:
                    constant = getattr(sqlite3, constant_name, None)
                    if constant is not None:
                        connection.setlimit(constant, value)

            deadline = BIPipeline._monotonic() + (settings.BI_SQL_TIMEOUT_MS / 1000)

            def progress_handler() -> int:
                nonlocal timed_out
                if BIPipeline._monotonic() >= deadline:
                    timed_out = True
                    return 1
                return 0

            def authorizer(action, arg1, arg2, _database, _trigger) -> int:
                nonlocal denied_function
                if action == sqlite3.SQLITE_SELECT:
                    return sqlite3.SQLITE_OK
                if action == sqlite3.SQLITE_READ:
                    return sqlite3.SQLITE_OK if (arg1 or "").lower() == "dataset" else sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_FUNCTION:
                    function_name = (arg2 or arg1 or "").lower()
                    if function_name in _ALLOWED_SQL_FUNCTIONS:
                        return sqlite3.SQLITE_OK
                    denied_function = function_name or "unknown"
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_DENY

            connection.set_authorizer(authorizer)
            connection.set_progress_handler(progress_handler, _SQL_PROGRESS_OPCODES)
            bounded_sql = f"SELECT * FROM ({sql}) AS bounded_result LIMIT {_MAX_RESULT_ROWS}"
            cursor = connection.execute(bounded_sql)
            rows = cursor.fetchmany(_MAX_RESULT_ROWS + 1)[:_MAX_RESULT_ROWS]
            columns = [description[0] for description in cursor.description or ()]
            return pd.DataFrame.from_records(rows, columns=columns)
        except sqlite3.DatabaseError as exc:
            if timed_out or "interrupted" in str(exc).lower():
                raise ValueError(
                    f"Query exceeded the {settings.BI_SQL_TIMEOUT_MS} ms execution limit"
                ) from None
            if denied_function:
                raise ValueError(f"SQL function '{denied_function}' is not allowed") from None
            raise ValueError("Query was blocked by the SQL safety policy") from None
        finally:
            connection.set_progress_handler(None, 0)
            connection.set_authorizer(None)
            connection.close()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _monotonic() -> float:
        return time.monotonic()

    @staticmethod
    def _schema(df: pd.DataFrame) -> str:
        lines = [f"  {col} ({dtype})" for col, dtype in df.dtypes.items()]
        return "Columns:\n" + "\n".join(lines)

    @staticmethod
    def _extract_chart(text: str) -> dict | None:
        match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except Exception:
                return None
        return None

    @staticmethod
    def _extract_sql(text: str) -> str | None:
        match = re.search(r"```sql\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()
        match = re.search(r"(SELECT\s+.+?)(;|$)", text, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return None

    @staticmethod
    def _normalize_sqlite_sql(sql: str) -> str:
        """Convert a small set of common model-generated date expressions to SQLite."""
        date_parts = {
            "hour": ("%H", "INTEGER"),
            "day": ("%d", "INTEGER"),
            "month": ("%m", "INTEGER"),
            "year": ("%Y", "INTEGER"),
            "dow": ("%w", "INTEGER"),
            "dayofweek": ("%w", "INTEGER"),
        }
        expression = re.compile(
            r"EXTRACT\s*\(\s*(HOUR|DAY|MONTH|YEAR|DOW|DAYOFWEEK)\s+FROM\s+"
            r"([A-Za-z_][A-Za-z0-9_]*|\"[^\"]+\")\s*\)",
            re.IGNORECASE,
        )

        def replace(match: re.Match) -> str:
            fmt, cast_type = date_parts[match.group(1).lower()]
            column = match.group(2)
            return f"CAST(strftime('{fmt}', {column}) AS {cast_type})"

        return expression.sub(replace, sql)

    @staticmethod
    def _build_chart(rows: list[dict], hint: dict, question: str) -> dict | None:
        if not rows or len(list(rows[0].keys())) < 2:
            return None
        cols = list(rows[0].keys())
        label_col = cols[0]
        numeric_cols = [
            col for col in cols[1:]
            if any(isinstance(row.get(col), (int, float)) for row in rows)
        ]
        if not numeric_cols:
            return None

        chart_type = hint.get("chart_type", "bar")
        if chart_type not in {"bar", "line", "pie", "scatter"}:
            chart_type = "bar"

        return {
            "chart_type": chart_type,
            "title": str(hint.get("title") or question[:60]),
            "x_label": str(hint.get("x_label") or label_col),
            "y_label": str(hint.get("y_label") or "Value"),
            "data": {
                "labels": [str(row.get(label_col, "")) for row in rows],
                "series": [
                    {
                        "name": col,
                        "values": [
                            float(row[col]) if isinstance(row.get(col), (int, float)) else 0
                            for row in rows
                        ],
                    }
                    for col in numeric_cols
                ],
            }
        }

    @staticmethod
    def _clean_answer(text: str) -> str:
        cleaned = re.sub(r"```(?:sql|json)\s*.*?```", "", text, flags=re.DOTALL | re.IGNORECASE)
        cleaned = re.sub(
            r"(?im)^\s*(?:here (?:is|are).*?(?:sql|json|chart).*?:?|sql query:|chart json:)\s*$",
            "",
            cleaned,
        )
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        return cleaned or "I prepared a query for this request."

    @classmethod
    def _result_answer(cls, rows: list[dict], has_chart: bool) -> str:
        count = len(rows)
        if not rows:
            return "No matching rows were found."

        prefix = "I generated the chart. " if has_chart else ""
        if count == 1:
            return prefix + cls._summarize_row(rows[0]) + "."

        shown = rows[:3]
        columns = list(shown[0])
        if len(columns) == 2:
            label_column, value_column = columns
            details = "; ".join(
                f"{cls._format_result_value(row.get(label_column), label_column)} — "
                f"{cls._humanize_column(value_column)} "
                f"{cls._format_result_value(row.get(value_column), value_column)}"
                for row in shown
            )
        else:
            details = "; ".join(
                f"Row {index}: {cls._summarize_row(row)}"
                for index, row in enumerate(shown, start=1)
            )
        remainder = f"; and {count - len(shown)} more rows" if count > len(shown) else ""
        noun = "row" if count == 1 else "rows"
        return f"{prefix}{count} {noun}: {details}{remainder}."

    @staticmethod
    def _history_text(history: list[dict] | None) -> str:
        """Bound prior chat context so follow-ups resolve without unbounded prompts."""
        if not history:
            return "(no prior conversation)"

        messages: list[str] = []
        remaining = min(4000, settings.MAX_PROMPT_CHARS // 2)
        for item in reversed(history[-8:]):
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").lower()
            if role not in {"user", "assistant"}:
                continue
            content = re.sub(r"\s+", " ", str(item.get("content") or "")).strip()
            if content:
                message = f"{role.upper()}: {content[:800]}"
                if len(message) + 1 > remaining:
                    continue
                messages.append(message)
                remaining -= len(message) + 1
        messages.reverse()
        return "\n".join(messages) or "(no prior conversation)"

    @classmethod
    def _summarize_row(cls, row: dict) -> str:
        entries = list(row.items())
        summary = "; ".join(
            f"{cls._humanize_column(column)} is {cls._format_result_value(value, column)}"
            for column, value in entries[:6]
        )
        if len(entries) > 6:
            summary += f"; plus {len(entries) - 6} more columns"
        return summary or "The query returned an empty result row"

    @staticmethod
    def _humanize_column(column: object) -> str:
        return re.sub(r"\s+", " ", str(column).replace("_", " ")).strip().lower()

    @staticmethod
    def _format_result_value(value: object, column: object = "") -> str:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return "null"
        if isinstance(value, bool):
            return "yes" if value else "no"
        if isinstance(value, numbers.Number):
            numeric = float(value)
            if not math.isfinite(numeric):
                return str(value)
            if numeric.is_integer():
                formatted = f"{int(numeric):,}"
            else:
                formatted = f"{numeric:,.2f}".rstrip("0").rstrip(".")
            label = str(column).lower()
            if "percent" in label or re.search(r"(^|_)pct($|_)", label):
                formatted += "%"
            return formatted
        text = re.sub(r"\s+", " ", str(value)).strip()
        return text if len(text) <= 120 else text[:117] + "..."

    @staticmethod
    def _validate_name(name: str) -> str:
        if not isinstance(name, str):
            raise BIInputError("Dataset name must be a string")
        name = (name or "").strip()
        if not _NAME_RE.match(name):
            raise BIInputError("Dataset name must start with a letter or underscore and use only letters, numbers, and underscores")
        return name

    @staticmethod
    def _normalize_name(name: str) -> str:
        """Turn an uploaded filename/display name into a safe dataset identifier."""
        normalized = re.sub(r"[^A-Za-z0-9_]+", "_", (name or "").strip())
        normalized = re.sub(r"_+", "_", normalized).strip("_").lower()
        if not normalized:
            normalized = "dataset"
        if normalized[0].isdigit():
            normalized = f"dataset_{normalized}"
        return BIPipeline._validate_name(normalized[:64].rstrip("_") or "dataset")

    @staticmethod
    def _validate_dataframe(df: pd.DataFrame) -> None:
        if len(df) > settings.MAX_DATASET_ROWS:
            raise ValueError(f"Dataset exceeds the {settings.MAX_DATASET_ROWS}-row limit")
        if len(df.columns) > settings.MAX_DATASET_COLUMNS:
            raise ValueError(f"Dataset exceeds the {settings.MAX_DATASET_COLUMNS}-column limit")
        memory_bytes = BIPipeline._dataframe_memory_bytes(df)
        if memory_bytes > settings.MAX_DATASET_MEMORY_BYTES:
            raise ValueError(
                f"Dataset exceeds the {settings.MAX_DATASET_MEMORY_BYTES}-byte in-memory limit"
            )

    @staticmethod
    def _validate_select_sql(sql: str) -> str:
        normalized = sql.strip().rstrip(";").strip()
        if len(normalized) > settings.BI_MAX_SQL_CHARS:
            raise ValueError(f"SQL exceeds the {settings.BI_MAX_SQL_CHARS}-character limit")
        if ";" in normalized:
            raise ValueError("Only one SQL statement is allowed")
        if not normalized.lower().startswith("select"):
            raise ValueError("Only SELECT queries are allowed")
        masked = BIPipeline._mask_sql_literals(normalized)
        if _DANGEROUS_SQL.search(masked):
            raise ValueError("SQL contains a blocked keyword")
        if _SQL_COMMENT.search(masked):
            raise ValueError("SQL comments are not allowed")
        if _FORBIDDEN_QUERY_SHAPES.search(masked):
            raise ValueError("Joins, compound queries, and CTEs are not allowed")
        functions = {match.group(1).lower() for match in _SQL_FUNCTION_CALL.finditer(masked)}
        blocked_functions = sorted(functions - _ALLOWED_SQL_FUNCTIONS - _SQL_PAREN_KEYWORDS)
        if blocked_functions:
            raise ValueError(f"SQL function '{blocked_functions[0]}' is not allowed")

        from_matches = list(re.finditer(r"\bfrom\b", masked, re.IGNORECASE))
        if len(from_matches) != 1:
            raise ValueError("Query must read from exactly one dataset table")

        remainder = re.sub(r"\s+", " ", masked[from_matches[0].end():]).strip()
        table = re.match(r"dataset\b", remainder, re.IGNORECASE)
        if not table:
            raise ValueError("Query may only read from the dataset table")
        remainder = remainder[table.end():].strip()

        clauses = ("where", "group by", "having", "order by", "limit", "offset")
        if remainder.lower().startswith("as "):
            alias = re.match(r"as\s+[A-Za-z_][A-Za-z0-9_]*\b", remainder, re.IGNORECASE)
            if not alias:
                raise ValueError("Invalid dataset alias")
            remainder = remainder[alias.end():].strip()
        elif remainder and not remainder.lower().startswith(clauses):
            alias = re.match(r"[A-Za-z_][A-Za-z0-9_]*\b", remainder)
            if not alias:
                raise ValueError("Query may only read from one dataset table")
            remainder = remainder[alias.end():].strip()

        if remainder and not remainder.lower().startswith(clauses):
            raise ValueError("Query may only read from one dataset table")
        return normalized

    @staticmethod
    def _mask_sql_literals(sql: str) -> str:
        literal = re.compile(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"")
        return literal.sub(lambda match: " " * len(match.group(0)), sql)

    @staticmethod
    def _dataframe_memory_bytes(df: pd.DataFrame) -> int:
        return int(df.memory_usage(index=True, deep=True).sum())

    @staticmethod
    def _validate_upload_size(path: str) -> int:
        try:
            size = Path(path).stat().st_size
        except OSError as exc:
            raise ValueError("Dataset upload could not be read") from exc
        if size > settings.MAX_DATASET_UPLOAD_BYTES:
            raise ValueError(f"Upload exceeds the {settings.MAX_DATASET_UPLOAD_BYTES}-byte dataset limit")
        return size

    @staticmethod
    def _validate_excel_archive(path: str) -> None:
        if Path(path).suffix.lower() != ".xlsx":
            return
        try:
            with zipfile.ZipFile(path) as archive:
                members = archive.infolist()
                if len(members) > settings.MAX_EXCEL_ARCHIVE_FILES:
                    raise ValueError(
                        f"Spreadsheet archive exceeds the {settings.MAX_EXCEL_ARCHIVE_FILES}-file limit"
                    )
                names = [member.filename for member in members]
                if len(names) != len(set(names)):
                    raise ValueError("Spreadsheet archive contains duplicate entries")
                if any(member.flag_bits & 0x1 for member in members):
                    raise ValueError("Encrypted spreadsheet archives are not supported")
                if any(
                    Path(name).is_absolute() or ".." in Path(name.replace("\\", "/")).parts
                    for name in names
                ):
                    raise ValueError("Spreadsheet archive contains an unsafe path")

                uncompressed = sum(member.file_size for member in members)
                compressed = sum(member.compress_size for member in members)
                if uncompressed > settings.MAX_EXCEL_UNCOMPRESSED_BYTES:
                    raise ValueError(
                        "Spreadsheet archive exceeds the configured uncompressed-size limit"
                    )
                ratio = uncompressed / max(compressed, 1)
                if ratio > settings.MAX_EXCEL_COMPRESSION_RATIO:
                    raise ValueError("Spreadsheet archive compression ratio is too high")
        except zipfile.BadZipFile as exc:
            raise ValueError("Invalid spreadsheet archive") from exc

    def _get_dataset(
        self,
        name: str,
        user_id: str,
        *,
        nrows: int | None = None,
    ) -> pd.DataFrame | None:
        """Return the current durable dataset without retaining it globally."""
        key = self._dataset_key(name, user_id)
        with _MANIFEST_LOCK:
            cached = _datasets.get(key)
            cached_version = _dataset_versions.get(key)

        record = dataset_repository.fetch(user_id, name)
        if record is None:
            # Direct cache injection is kept only for isolated/legacy tests.
            if cached is not None and cached_version is None:
                return cached
            with _MANIFEST_LOCK:
                _datasets.pop(key, None)
                _dataset_versions.pop(key, None)
            return None
        stream = BytesIO(record["payload"])
        row_limit = nrows if nrows is not None else settings.MAX_DATASET_ROWS + 1
        if record["kind"] == "csv":
            frame = pd.read_csv(stream, nrows=row_limit)
        elif record["kind"] == "excel":
            frame = pd.read_excel(stream, nrows=row_limit)
        else:
            raise ValueError("Stored dataset type is unsupported")
        self._validate_dataframe(frame)
        return frame

    def _load_manifest(self):
        """Import pre-database manifests once, preserving compatible local data."""
        with _MANIFEST_LOCK:
            records = self._read_manifest_unlocked()
            migrated: list[dict] = []
            remaining: list[dict] = []
            for record in records:
                try:
                    name = self._validate_name(record["name"])
                    user_id = record.get("user_id", "local")
                    path = record["path"]
                    kind = record["kind"]
                    self._validate_upload_size(path)
                    if kind == "csv":
                        df = pd.read_csv(path, nrows=settings.MAX_DATASET_ROWS + 1)
                    elif kind == "excel":
                        self._validate_excel_archive(path)
                        df = pd.read_excel(path, nrows=settings.MAX_DATASET_ROWS + 1)
                    else:
                        continue
                    self._validate_dataframe(df)
                    dataset_repository.upsert(
                        user_id=user_id,
                        name=name,
                        kind=kind,
                        payload=Path(path).read_bytes(),
                        row_count=len(df),
                        columns=[str(column) for column in df.columns],
                        max_datasets=settings.MAX_DATASETS_PER_USER,
                        max_storage_bytes=settings.MAX_DATASET_STORAGE_BYTES_PER_USER,
                        max_total_storage_bytes=settings.MAX_DATASET_STORAGE_BYTES_TOTAL,
                    )
                    migrated.append(record)
                except Exception:
                    remaining.append(record)
            if migrated:
                self._write_manifest_unlocked(remaining)
                self._remove_unreferenced_uploads(migrated, remaining)

    def _save_dataset_record(self, name: str, path: str, kind: str, user_id: str = "local"):
        """Compatibility helper for record-only callers; uploads use _commit_dataset."""
        with self._frame_slot():
            self._replace_dataset_record(name, path, kind, user_id=user_id, df=None)

    def _commit_dataset(
        self,
        name: str,
        path: str,
        kind: str,
        df: pd.DataFrame,
        user_id: str = "local",
        unique: bool = False,
    ) -> str:
        return self._replace_dataset_record(
            name,
            path,
            kind,
            user_id=user_id,
            df=df,
            unique=unique,
        )

    def _replace_dataset_record(
        self,
        name: str,
        path: str,
        kind: str,
        *,
        user_id: str,
        df: pd.DataFrame | None,
        unique: bool = False,
    ) -> str:
        if kind not in {"csv", "excel"}:
            raise ValueError("Unsupported dataset kind")
        path = str(Path(path))
        new_size = self._validate_upload_size(path)
        if df is None:
            if kind == "csv":
                df = pd.read_csv(path, nrows=settings.MAX_DATASET_ROWS + 1)
            else:
                self._validate_excel_archive(path)
                df = pd.read_excel(path, nrows=settings.MAX_DATASET_ROWS + 1)
            self._validate_dataframe(df)
        new_memory = self._dataframe_memory_bytes(df)
        payload = Path(path).read_bytes()

        with _MANIFEST_LOCK:
            records = self._read_manifest_unlocked(strict=True)
            if new_memory > settings.MAX_DATASET_MEMORY_BYTES_PER_USER:
                raise ValueError(
                    "User exceeds the configured cumulative in-memory dataset limit"
                )

            name = dataset_repository.upsert(
                user_id=user_id,
                name=name,
                kind=kind,
                payload=payload,
                row_count=len(df),
                columns=[str(column) for column in df.columns],
                max_datasets=settings.MAX_DATASETS_PER_USER,
                max_storage_bytes=settings.MAX_DATASET_STORAGE_BYTES_PER_USER,
                max_total_storage_bytes=settings.MAX_DATASET_STORAGE_BYTES_TOTAL,
                unique=unique,
            )
            key = self._dataset_key(name, user_id)
            replaced = [
                record for record in records
                if record.get("name") == name and record.get("user_id", "local") == user_id
            ]
            remaining = [
                record for record in records
                if not (
                    record.get("name") == name
                    and record.get("user_id", "local") == user_id
                )
            ]
            # New uploads no longer depend on a local manifest or Render disk.
            if replaced:
                self._write_manifest_unlocked(remaining)
            # Do not retain durable frames after upload. Each ask keeps only a
            # request-local reference, bounding worker memory under public use.
            _datasets.pop(key, None)
            _dataset_versions.pop(key, None)
            self._remove_unreferenced_uploads(replaced, remaining)
            return name

    @staticmethod
    def _read_manifest_unlocked(*, strict: bool = False) -> list[dict]:
        if not _manifest.exists():
            return []
        try:
            records = json.loads(_manifest.read_text(encoding="utf-8"))
            if not isinstance(records, list) or not all(isinstance(record, dict) for record in records):
                raise ValueError("Manifest must contain a list of records")
            return records
        except Exception as exc:
            if strict:
                raise ValueError("Dataset manifest could not be read safely") from exc
            return []

    @staticmethod
    def _write_manifest_unlocked(records: list[dict]) -> None:
        _manifest.parent.mkdir(parents=True, exist_ok=True)
        temporary = _manifest.with_suffix(_manifest.suffix + ".tmp")
        temporary.write_text(json.dumps(records, indent=2), encoding="utf-8")
        temporary.replace(_manifest)

    @staticmethod
    def _record_size(record: dict) -> int:
        try:
            return Path(str(record.get("path", ""))).stat().st_size
        except OSError:
            try:
                return max(0, int(record.get("size_bytes", 0)))
            except (TypeError, ValueError):
                return 0

    @staticmethod
    def _remove_unreferenced_uploads(removed: list[dict], remaining: list[dict]) -> None:
        try:
            upload_root = Path(settings.UPLOAD_PATH).resolve()
        except (OSError, RuntimeError):
            return
        referenced: set[str] = set()
        for record in remaining:
            if not record.get("path"):
                continue
            try:
                referenced.add(str(Path(str(record["path"])).resolve()))
            except (OSError, RuntimeError):
                continue
        for record in removed:
            if not record.get("path"):
                continue
            try:
                old_path = Path(str(record["path"])).resolve()
                if str(old_path) not in referenced and old_path.is_relative_to(upload_root):
                    old_path.unlink(missing_ok=True)
            except (OSError, RuntimeError):
                pass


bi_pipeline = BIPipeline()
