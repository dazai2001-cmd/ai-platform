from core.config.constants import DEFAULT_CLOUD_TASK_MODEL_MAP, DEFAULT_TASK_MODEL_MAP
from core.config.settings import settings
from services.storage.sqlite_service import db


class ModelSettingsService:
    def _defaults(self) -> dict:
        if settings.IS_CLOUD_RUNTIME:
            defaults = {task: settings.CLOUD_DEFAULT_MODEL for task in DEFAULT_CLOUD_TASK_MODEL_MAP}
        else:
            defaults = dict(DEFAULT_TASK_MODEL_MAP)
        defaults.update({
            task: model
            for task, model in settings.TASK_MODEL_OVERRIDES.items()
            if task in defaults
        })
        return defaults

    def _available_models(self) -> set[str]:
        if not settings.IS_CLOUD_RUNTIME:
            return set()
        models = []
        if settings.GEMINI_API_KEY:
            models.extend(f"gemini:{model}" for model in settings.GEMINI_MODELS)
        if settings.OPENROUTER_API_KEY:
            models.extend(f"openrouter:{model}" for model in settings.OPENROUTER_MODELS)
        return set(models)

    def available_models(self) -> list[str]:
        return sorted(self._available_models())

    def _valid_model(self, model: str) -> bool:
        if not settings.IS_CLOUD_RUNTIME:
            return bool(model) and (
                not settings.IS_PRODUCTION
                or model in set(settings.LOCAL_ALLOWED_MODELS)
            )
        return model in self._available_models()

    def __init__(self):
        self._apply_saved()

    def get(self, user_id: str = "local") -> dict:
        models = dict(settings.TASK_MODELS if user_id == "local" else self._defaults())
        saved = {
            row["task"]: row["model"]
            for row in db.query(
                "SELECT task, model FROM user_model_settings WHERE user_id = ?",
                (user_id,),
            )
        }
        for task, model in saved.items():
            if task in self._defaults() and self._valid_model(model):
                models[task] = model
        return models

    def update(self, task_models: dict[str, str], user_id: str = "local") -> dict:
        defaults = self._defaults()
        if not isinstance(task_models, dict) or any(
            task not in defaults
            or not isinstance(model, str)
            or not self._valid_model(model.strip())
            for task, model in task_models.items()
        ):
            raise ValueError("one or more task models are not in the configured allow-list")
        clean = {
            task: model.strip()
            for task, model in task_models.items()
            if task in defaults and isinstance(model, str) and self._valid_model(model.strip())
        }
        if not clean:
            return self.get(user_id=user_id)

        if user_id == "local":
            settings.TASK_MODELS.update(clean)
        current = self.get(user_id=user_id)
        current.update(clean)
        db.execute_many([
            (
                """
                INSERT INTO user_model_settings (user_id, task, model) VALUES (?, ?, ?)
                ON CONFLICT (user_id, task) DO UPDATE SET model = excluded.model
                """,
                (user_id, task, model),
            )
            for task, model in current.items()
        ])
        return self.get(user_id=user_id)

    def reset(self, user_id: str = "local") -> dict:
        db.execute("DELETE FROM user_model_settings WHERE user_id = ?", (user_id,))
        if user_id == "local":
            settings.TASK_MODELS.clear()
            settings.TASK_MODELS.update(self._defaults())
            db.execute("DELETE FROM model_settings")
        return self.get(user_id=user_id)

    def model_for(self, task: str, user_id: str = "local") -> str:
        models = self.get(user_id=user_id)
        return models.get(task) or models.get("general")

    def resolve_model(self, task: str, requested: str | None = None, user_id: str = "local") -> str:
        model = (requested or "").strip() or self.model_for(task, user_id=user_id)
        if not self._valid_model(model):
            raise ValueError("model is not in the configured allow-list")
        return model

    def _apply_saved(self):
        saved = {
            row["task"]: row["model"]
            for row in db.query("SELECT task, model FROM model_settings")
        }
        clean = {
            task: model
            for task, model in saved.items()
            if task in self._defaults() and self._valid_model(model)
        }
        settings.TASK_MODELS.update(clean)
        db.execute_many([
            (
                """
                INSERT INTO user_model_settings (user_id, task, model) VALUES ('local', ?, ?)
                ON CONFLICT (user_id, task) DO UPDATE SET model = excluded.model
                """,
                (task, model),
            )
            for task, model in clean.items()
        ])


model_settings = ModelSettingsService()
