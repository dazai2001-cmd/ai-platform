from core.config.constants import DEFAULT_TASK_MODEL_MAP
from core.config.settings import settings
from services.storage.sqlite_service import db


class ModelSettingsService:
    def __init__(self):
        self._apply_saved()

    def get(self, user_id: str = "local") -> dict:
        models = dict(settings.TASK_MODELS if user_id == "local" else DEFAULT_TASK_MODEL_MAP)
        saved = {
            row["task"]: row["model"]
            for row in db.query(
                "SELECT task, model FROM user_model_settings WHERE user_id = ?",
                (user_id,),
            )
        }
        for task, model in saved.items():
            if task in DEFAULT_TASK_MODEL_MAP and model:
                models[task] = model
        return models

    def update(self, task_models: dict[str, str], user_id: str = "local") -> dict:
        clean = {
            task: model.strip()
            for task, model in task_models.items()
            if task in DEFAULT_TASK_MODEL_MAP and isinstance(model, str) and model.strip()
        }
        if not clean:
            return self.get(user_id=user_id)

        if user_id == "local":
            settings.TASK_MODELS.update(clean)
        current = self.get(user_id=user_id)
        current.update(clean)
        db.execute_many([
            (
                "INSERT OR REPLACE INTO user_model_settings (user_id, task, model) VALUES (?, ?, ?)",
                (user_id, task, model),
            )
            for task, model in current.items()
        ])
        return self.get(user_id=user_id)

    def reset(self, user_id: str = "local") -> dict:
        db.execute("DELETE FROM user_model_settings WHERE user_id = ?", (user_id,))
        if user_id == "local":
            settings.TASK_MODELS.clear()
            settings.TASK_MODELS.update(DEFAULT_TASK_MODEL_MAP)
            db.execute("DELETE FROM model_settings")
        return self.get(user_id=user_id)

    def model_for(self, task: str, user_id: str = "local") -> str:
        return self.get(user_id=user_id).get(task) or self.get(user_id=user_id).get("general")

    def _apply_saved(self):
        saved = {
            row["task"]: row["model"]
            for row in db.query("SELECT task, model FROM model_settings")
        }
        clean = {
            task: model
            for task, model in saved.items()
            if task in DEFAULT_TASK_MODEL_MAP and model
        }
        settings.TASK_MODELS.update(clean)
        db.execute_many([
            (
                "INSERT OR REPLACE INTO user_model_settings (user_id, task, model) VALUES ('local', ?, ?)",
                (task, model),
            )
            for task, model in clean.items()
        ])


model_settings = ModelSettingsService()
