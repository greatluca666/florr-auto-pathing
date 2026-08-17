def test_main_module_imports_and_exposes_enemy_config():
    import main
    assert hasattr(main, "auto_farming")
    assert hasattr(main, "ENEMY_SCAN_INTERVAL")
    assert hasattr(main, "AVOID_TRIGGER_PX")
    assert hasattr(main, "CAUTIOUS_HOLD_PX")
