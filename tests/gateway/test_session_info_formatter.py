from gateway.session_info import format_session_info


def test_format_session_info_uses_config_context_and_local_endpoint():
    info = format_session_info(
        resolve_gateway_model=lambda: "local-model",
        load_gateway_config=lambda: {
            "model": {
                "provider": "custom",
                "base_url": "http://localhost:11434/v1",
                "context_length": 8192,
            }
        },
        resolve_runtime_agent_kwargs=lambda: {},
    )

    assert "◆ Model: `local-model`" in info
    assert "◆ Provider: custom" in info
    assert "◆ Context: 8K tokens (config)" in info
    assert "◆ Endpoint: http://localhost:11434/v1" in info


def test_format_session_info_uses_custom_provider_model_context():
    info = format_session_info(
        resolve_gateway_model=lambda: "custom/narrow",
        load_gateway_config=lambda: {
            "custom_providers": [
                {
                    "provider": "custom",
                    "models": {"custom/narrow": {"context_length": 16384}},
                }
            ]
        },
        resolve_runtime_agent_kwargs=lambda: {"provider": "custom"},
    )

    assert "◆ Context: 16K tokens (config)" in info
