import json
import logging
import subprocess
from pathlib import Path
from datetime import datetime

log = logging.getLogger(__name__)

class UpdateManager:
    """
    Manages the update and rollback state of the application.
    Tracks 'last_stable_commit' and 'pending_commit'.
    """
    def __init__(self, config: dict):
        # Default state file location
        self.state_file = Path(config.get('paths', {}).get('update_state_file', '/home/cosigein/update_state.json'))
        self.state = self._load_state()

    def _load_state(self) -> dict:
        if not self.state_file.exists():
            return {
                "last_stable_commit": None,
                "pending_commit": None,
                "last_check_ts": None,
                "is_stable": False,
                "instability_reason": None
            }
        try:
            with open(self.state_file, 'r') as f:
                return json.load(f)
        except Exception as e:
            log.error(f"UpdateManager: Error al cargar estado: {e}")
            return {}

    def _save_state(self):
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.state_file, 'w') as f:
                json.dump(self.state, f, indent=4)
        except Exception as e:
            log.error(f"UpdateManager: Error al guardar estado: {e}")

    def mark_as_stable(self):
        """Called when the current version passes startup self-tests and initial sanity."""
        current_commit = self._get_current_commit()
        if not current_commit:
            log.warning("UpdateManager: No se pudo determinar el commit actual.")
            return

        self.state["last_stable_commit"] = current_commit
        self.state["pending_commit"] = None
        self.state["is_stable"] = True
        self.state["last_check_ts"] = datetime.now().isoformat()
        self._save_state()
        log.info(f"UpdateManager: Versión {current_commit[:8]} marcada como ESTABLE.")

    def mark_as_unstable(self, reason: str):
        """Called if a session health check fails, triggering rollback on next boot."""
        self.state["is_stable"] = False
        self.state["instability_reason"] = reason
        self.state["last_check_ts"] = datetime.now().isoformat()
        self._save_state()
        log.warning(f"UpdateManager: Sistema marcado como INESTABLE. Motivo: {reason}")

    def set_pending_update(self, commit_hash: str):
        """Called after a successful update pull but before marked as stable."""
        self.state["pending_commit"] = commit_hash
        self.state["is_stable"] = False
        self._save_state()
        log.info(f"UpdateManager: Nueva versión {commit_hash[:8]} marcada como PENDIENTE.")

    def _get_current_commit(self) -> str:
        try:
            # We assume we are in the repo directory or know where it is
            # In setup.py we know it. In the app, it's APP_DIR.
            # For simplicity, we can try to run git rev-parse HEAD in the current dir
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return None

    def get_state(self) -> dict:
        return self.state
