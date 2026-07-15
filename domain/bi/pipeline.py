import json
import re
import sqlite3
import threading
import time
import zipfile
import pandas as pd
from pathlib import Path
from infrastructure.llm.ollama_client import ollama
from core.config.settings import settings

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

# In-memory dataset store: {name: pd.DataFrame}
_datasets: dict[str, pd.DataFrame] = {}
_manifest = Path(settings.BI_MANIFEST_PATH)
_MANIFEST_LOCK = threading.RLock()


class BIPipeline:
    def __init__(self):
        self._load_manifest()

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _dataset_key(self, name: str, user_id: str = "local") -> str:
        return f"{user_id}:{name}"

    def load_csv(self, path: str, name: str, user_id: str = "local") -> dict:
        name = self._validate_name(name)
        self._validate_upload_size(path)
        df = pd.read_csv(path, nrows=settings.MAX_DATASET_ROWS + 1)
        self._validate_dataframe(df)
        self._commit_dataset(name, path, "csv", df, user_id=user_id)
        return {"name": name, "rows": len(df), "columns": list(df.columns)}

    def load_excel(self, path: str, name: str, user_id: str = "local") -> dict:
        name = self._validate_name(name)
        self._validate_upload_size(path)
        self._validate_excel_archive(path)
        df = pd.read_excel(path, nrows=settings.MAX_DATASET_ROWS + 1)
        self._validate_dataframe(df)
        self._commit_dataset(name, path, "excel", df, user_id=user_id)
        return {"name": name, "rows": len(df), "columns": list(df.columns)}

    def list_datasets(self, user_id: str = "local") -> list[dict]:
        with _MANIFEST_LOCK:
            return [
                {"name": key.split(":", 1)[1], "rows": len(df), "columns": list(df.columns)}
                for key, df in _datasets.items()
                if key.startswith(f"{user_id}:")
            ]

    def get_sample(self, name: str, user_id: str = "local") -> dict | None:
        with _MANIFEST_LOCK:
            df = _datasets.get(self._dataset_key(name, user_id))
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
            if not removed and key not in _datasets:
                return False
            if removed:
                self._write_manifest_unlocked(remaining)
            _datasets.pop(key, None)
            self._remove_unreferenced_uploads(removed, remaining)
            return True

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def ask(self, question: str, dataset_name: str = None, model: str = None, user_id: str = "local") -> dict:
        model = model or settings.TASK_MODELS["bi"]
        with _MANIFEST_LOCK:
            user_keys = [key for key in _datasets if key.startswith(f"{user_id}:")]
            # Pick dataset and retain a stable object reference if another upload
            # replaces the same name while this query is running.
            display_name = dataset_name or (user_keys[0].split(":", 1)[1] if user_keys else None)
            key = self._dataset_key(display_name, user_id) if display_name else None
            df = _datasets.get(key) if key else None
        if df is None:
            return {"answer": "No dataset loaded. Please upload a CSV or Excel file first.", "chart": None}
        schema = self._schema(df)
        sample = df.head(5).to_string(index=False)

        prompt = _PROMPT.format(schema=schema, sample=sample, question=question)
        response = ollama.generate(model, prompt, temperature=0.1)

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

    @staticmethod
    def _result_answer(rows: list[dict], has_chart: bool) -> str:
        count = len(rows)
        noun = "row" if count == 1 else "rows"
        if has_chart:
            return f"Chart generated from {count} query result {noun}."
        return f"Query completed and returned {count} {noun}."

    @staticmethod
    def _validate_name(name: str) -> str:
        name = (name or "").strip()
        if not _NAME_RE.match(name):
            raise ValueError("Dataset name must start with a letter or underscore and use only letters, numbers, and underscores")
        return name

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

    def _load_manifest(self):
        with _MANIFEST_LOCK:
            records = self._read_manifest_unlocked()
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
                    key = self._dataset_key(name, user_id)
                    other_memory = sum(
                        self._dataframe_memory_bytes(existing)
                        for existing_key, existing in _datasets.items()
                        if existing_key.startswith(f"{user_id}:") and existing_key != key
                    )
                    if other_memory + self._dataframe_memory_bytes(df) > settings.MAX_DATASET_MEMORY_BYTES_PER_USER:
                        continue
                    _datasets[key] = df
                except Exception:
                    continue

    def _save_dataset_record(self, name: str, path: str, kind: str, user_id: str = "local"):
        """Compatibility helper for record-only callers; uploads use _commit_dataset."""
        self._replace_dataset_record(name, path, kind, user_id=user_id, df=None)

    def _commit_dataset(
        self,
        name: str,
        path: str,
        kind: str,
        df: pd.DataFrame,
        user_id: str = "local",
    ) -> None:
        self._replace_dataset_record(name, path, kind, user_id=user_id, df=df)

    def _replace_dataset_record(
        self,
        name: str,
        path: str,
        kind: str,
        *,
        user_id: str,
        df: pd.DataFrame | None,
    ) -> None:
        if kind not in {"csv", "excel"}:
            raise ValueError("Unsupported dataset kind")
        path = str(Path(path))
        new_size = self._validate_upload_size(path)
        new_memory = self._dataframe_memory_bytes(df) if df is not None else 0
        key = self._dataset_key(name, user_id)

        with _MANIFEST_LOCK:
            records = self._read_manifest_unlocked(strict=True)
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
            user_records = [
                record for record in remaining
                if record.get("user_id", "local") == user_id
            ]
            if len(user_records) + 1 > settings.MAX_DATASETS_PER_USER:
                raise ValueError(
                    f"User exceeds the {settings.MAX_DATASETS_PER_USER}-dataset limit"
                )

            storage_bytes = sum(self._record_size(record) for record in user_records) + new_size
            if storage_bytes > settings.MAX_DATASET_STORAGE_BYTES_PER_USER:
                raise ValueError(
                    "User exceeds the configured cumulative dataset storage limit"
                )

            if df is not None:
                other_memory = sum(
                    self._dataframe_memory_bytes(existing)
                    for existing_key, existing in _datasets.items()
                    if existing_key.startswith(f"{user_id}:") and existing_key != key
                )
                if other_memory + new_memory > settings.MAX_DATASET_MEMORY_BYTES_PER_USER:
                    raise ValueError(
                        "User exceeds the configured cumulative in-memory dataset limit"
                    )

            updated = remaining + [{
                "name": name,
                "path": path,
                "kind": kind,
                "user_id": user_id,
                "size_bytes": new_size,
                "memory_bytes": new_memory,
            }]
            # The manifest replacement and in-memory swap are serialized in one
            # critical section, so readers can only observe the old or new pair.
            self._write_manifest_unlocked(updated)
            if df is not None:
                _datasets[key] = df
            self._remove_unreferenced_uploads(replaced, updated)

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
