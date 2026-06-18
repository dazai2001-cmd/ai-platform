from core.config.constants import DEFAULT_TASK_MODEL_MAP
from core.config.settings import settings
from services.storage.sqlite_service import db


class ModelSettingsService:
    def __init__(self):
        self._apply_saved()

    def get(self) -> dict:
        return dict(settings.TASK_MODELS)

    def update(self, task_models: dict[str, str]) -> dict:
        clean = {
            task: model.strip()
            for task, model in task_models.items()
            if task in DEFAULT_TASK_MODEL_MAP and isinstance(model, str) and model.strip()
        }
        if not clean:
            return self.get()

        settings.TASK_MODELS.update(clean)
        db.execute_many([
            (
                "INSERT OR REPLACE INTO model_settings (task, model) VALUES (?, ?)",
                (task, model),
            )
            for task, model in settings.TASK_MODELS.items()
        ])
        return self.get()

    def reset(self) -> dict:
        settings.TASK_MODELS.clear()
        settings.TASK_MODELS.update(DEFAULT_TASK_MODEL_MAP)
        db.execute("DELETE FROM model_settings")
        return self.get()

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


model_settings = ModelSettingsService()
