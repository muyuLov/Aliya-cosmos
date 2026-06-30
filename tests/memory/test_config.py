"""测试 config 模块的配置加载、类型校验、热重载"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from memory.config import (
    ExtractorConfig,
    GRAGConfig,
    Neo4jConfig,
    TaskManagerConfig,
    _check_type,
    _load_grag_config,
    get_grag_config,
    reload_config,
)
from memory.exceptions import GRAGConfigError


class TestCheckType:
    def test_valid_type_passes(self):
        _check_type(42, "test.key", int)

    def test_invalid_type_raises(self):
        with pytest.raises(GRAGConfigError, match="期望类型"):
            _check_type("not_int", "test.key", int)

    def test_tuple_type_passes(self):
        _check_type(3.14, "test.key", (int, float))

    def test_min_value_ok(self):
        _check_type(5, "test.key", int, min_val=1)

    def test_below_min_raises(self):
        with pytest.raises(GRAGConfigError, match="小于最小值"):
            _check_type(0, "test.key", int, min_val=1)

    def test_max_value_ok(self):
        _check_type(0.5, "test.key", (int, float), max_val=1.0)

    def test_above_max_raises(self):
        with pytest.raises(GRAGConfigError, match="大于最大值"):
            _check_type(1.5, "test.key", (int, float), max_val=1.0)

    def test_boundary_values_pass(self):
        _check_type(1, "test.key", int, min_val=1)
        _check_type(0.0, "test.key", (int, float), min_val=0.0)


class TestDataclassDefaults:
    def test_neo4j_config_defaults(self):
        cfg = Neo4jConfig()
        assert cfg.uri == "bolt://localhost:7687"
        assert cfg.user == "neo4j"
        assert cfg.password is None
        assert cfg.database == "neo4j"
        assert cfg.max_connections == 10

    def test_extractor_config_defaults(self):
        cfg = ExtractorConfig()
        assert cfg.max_retries == 2
        assert cfg.timeout == 30

    def test_task_manager_config_defaults(self):
        cfg = TaskManagerConfig()
        assert cfg.max_workers == 3
        assert cfg.max_queue_size == 100
        assert cfg.task_timeout == 30
        assert cfg.auto_cleanup_hours == 24

    def test_grag_config_defaults(self):
        cfg = GRAGConfig()
        assert cfg.enabled is True
        assert cfg.auto_extract is True
        assert cfg.context_length == 10
        assert cfg.similarity_threshold == 0.7
        assert cfg.session_tracking is True
        assert isinstance(cfg.neo4j, Neo4jConfig)
        assert isinstance(cfg.extractor, ExtractorConfig)
        assert isinstance(cfg.task_manager, TaskManagerConfig)


class TestLoadGragConfig:
    @patch("memory.config.get_config_instance")
    def test_loads_full_config(self, mock_get_cfg):
        mock_cfg = mock_get_cfg.return_value
        mock_cfg.get.side_effect = lambda key, default=None: {
            "cosmos.service.grag.enabled": True,
            "cosmos.service.grag.auto_extract": True,
            "cosmos.service.grag.context_length": 20,
            "cosmos.service.grag.similarity_threshold": 0.8,
            "cosmos.service.grag.session_tracking": True,
            "cosmos.service.grag.neo4j": {
                "uri": "bolt://custom:7687",
                "user": "admin",
                "password": "secret",
                "database": "mydb",
                "max_connections": 20,
            },
            "cosmos.service.grag.extractor": {
                "max_retries": 3,
                "timeout": 60,
            },
            "cosmos.service.grag.task_manager": {
                "max_workers": 5,
                "max_queue_size": 200,
                "task_timeout": 60,
                "auto_cleanup_hours": 48,
            },
        }.get(key, default)

        cfg = _load_grag_config("test.yml")
        assert cfg.enabled is True
        assert cfg.context_length == 20
        assert cfg.similarity_threshold == 0.8
        assert cfg.neo4j.uri == "bolt://custom:7687"
        assert cfg.neo4j.password == "secret"
        assert cfg.extractor.max_retries == 3
        assert cfg.task_manager.max_workers == 5

    @patch("memory.config.get_config_instance")
    def test_fail_fast_on_missing_password(self, mock_get_cfg):
        mock_cfg = mock_get_cfg.return_value
        mock_cfg.get.side_effect = lambda key, default=None: {
            "cosmos.service.grag.enabled": True,
            "cosmos.service.grag.neo4j": {
                "uri": "bolt://localhost:7687",
                "user": "neo4j",
                "password": None,
            },
            "cosmos.service.grag.extractor": {},
            "cosmos.service.grag.task_manager": {},
        }.get(key, default)

        with pytest.raises(GRAGConfigError, match="必须配置 Neo4j 密码"):
            _load_grag_config("test.yml")

    @patch("memory.config.get_config_instance")
    def test_disabled_skips_password_check(self, mock_get_cfg):
        mock_cfg = mock_get_cfg.return_value
        mock_cfg.get.side_effect = lambda key, default=None: {
            "cosmos.service.grag.enabled": False,
            "cosmos.service.grag.neo4j": {},
            "cosmos.service.grag.extractor": {},
            "cosmos.service.grag.task_manager": {},
        }.get(key, default)

        cfg = _load_grag_config("test.yml")
        assert cfg.enabled is False
        assert cfg.neo4j.password is None

    @patch("memory.config.get_config_instance")
    def test_missing_sections_use_defaults(self, mock_get_cfg):
        mock_cfg = mock_get_cfg.return_value
        mock_cfg.get.side_effect = lambda key, default=None: {
            "cosmos.service.grag.enabled": False,
            "cosmos.service.grag.auto_extract": True,
            "cosmos.service.grag.context_length": 10,
            "cosmos.service.grag.similarity_threshold": 0.7,
            "cosmos.service.grag.session_tracking": True,
            "cosmos.service.grag.neo4j": {},
            "cosmos.service.grag.extractor": {},
            "cosmos.service.grag.task_manager": {},
        }.get(key, default)

        cfg = _load_grag_config("test.yml")
        assert cfg.context_length == 10
        assert cfg.neo4j.uri == "bolt://localhost:7687"
        assert cfg.extractor.max_retries == 2
        assert cfg.task_manager.max_workers == 3


class TestGetGragConfig:
    def test_returns_singleton(self):
        cfg1 = get_grag_config()
        cfg2 = get_grag_config()
        assert cfg1 is cfg2

    def test_reload_returns_new_instance(self):
        cfg1 = get_grag_config()
        cfg2 = reload_config()
        assert cfg1 is not cfg2
