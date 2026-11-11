"""Only catalog of selectable model backends; adapters load lazily."""
import importlib

REGISTRY = {"claude": (".claude_judge", "ClaudeJudge"), "codex": (".codex_judge", "CodexJudge")}


def adapter(name):
    module, cls = REGISTRY[name]
    return getattr(importlib.import_module(module, __package__), cls)


def validate(cfg):
    cls = adapter(cfg["backend"])
    validator = getattr(cls, "validate", None)
    if validator:
        validator(cfg)


def create(cfg, scratch_dir, log=None):
    instance = adapter(cfg["backend"])(cfg, scratch_dir, log)
    instance.backend = cfg["backend"]
    return instance
