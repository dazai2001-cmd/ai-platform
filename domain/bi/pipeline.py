import json
import re
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
_MAX_RESULT_ROWS = 200

# In-memory dataset store: {name: pd.DataFrame}
_datasets: dict[str, pd.DataFrame] = {}
_manifest = Path(settings.BI_MANIFEST_PATH)


class BIPipeline:
    def __init__(self):
        self._load_manifest()

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def load_csv(self, path: str, name: str) -> dict:
        name = self._validate_name(name)
        df = pd.read_csv(path)
        _datasets[name] = df
        self._save_dataset_record(name, path, "csv")
        return {"name": name, "rows": len(df), "columns": list(df.columns)}

    def load_excel(self, path: str, name: str) -> dict:
        name = self._validate_name(name)
        df = pd.read_excel(path)
        _datasets[name] = df
        self._save_dataset_record(name, path, "excel")
        return {"name": name, "rows": len(df), "columns": list(df.columns)}

    def list_datasets(self) -> list[dict]:
        return [
            {"name": n, "rows": len(df), "columns": list(df.columns)}
            for n, df in _datasets.items()
        ]

    def get_sample(self, name: str) -> dict | None:
        df = _datasets.get(name)
        if df is None:
            return None
        return {
            "columns": list(df.columns),
            "sample": df.head(5).to_dict(orient="records")
        }

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def ask(self, question: str, dataset_name: str = None, model: str = None) -> dict:
        model = model or settings.TASK_MODELS["bi"]
        # Pick dataset
        name = dataset_name or (next(iter(_datasets)) if _datasets else None)
        if not name or name not in _datasets:
            return {"answer": "No dataset loaded. Please upload a CSV or Excel file first.", "chart": None}

        df = _datasets[name]
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
                self._validate_select_sql(sql)
                import pandasql as psql
                result_df = psql.sqldf(sql, {"dataset": df})
                result_df = result_df.head(_MAX_RESULT_ROWS)
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
            "dataset": name
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

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
    def _validate_select_sql(sql: str):
        normalized = sql.strip().rstrip(";").strip()
        if ";" in normalized:
            raise ValueError("Only one SQL statement is allowed")
        if not normalized.lower().startswith("select"):
            raise ValueError("Only SELECT queries are allowed")
        if _DANGEROUS_SQL.search(normalized):
            raise ValueError("SQL contains a blocked keyword")

    def _load_manifest(self):
        if not _manifest.exists():
            return
        try:
            records = json.loads(_manifest.read_text(encoding="utf-8"))
        except Exception:
            return
        for record in records:
            try:
                name = self._validate_name(record["name"])
                path = record["path"]
                kind = record["kind"]
                if kind == "csv":
                    _datasets[name] = pd.read_csv(path)
                elif kind == "excel":
                    _datasets[name] = pd.read_excel(path)
            except Exception:
                continue

    def _save_dataset_record(self, name: str, path: str, kind: str):
        _manifest.parent.mkdir(parents=True, exist_ok=True)
        records = []
        if _manifest.exists():
            try:
                records = json.loads(_manifest.read_text(encoding="utf-8"))
            except Exception:
                records = []
        records = [r for r in records if r.get("name") != name]
        records.append({"name": name, "path": path, "kind": kind})
        _manifest.write_text(json.dumps(records, indent=2), encoding="utf-8")


bi_pipeline = BIPipeline()
