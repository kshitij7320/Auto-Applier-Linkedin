import pytest
from src.stealth import generate_bezier_trajectory, generate_overshoot_trajectory

def test_bezier_trajectory_generation():
    start = (100.0, 100.0)
    end = (500.0, 400.0)
    path = generate_bezier_trajectory(start, end, steps=30)
    assert len(path) == 30
    assert abs(path[0][0] - start[0]) < 1e-2
    assert abs(path[0][1] - start[1]) < 1e-2
    assert abs(path[-1][0] - end[0]) < 1e-2
    assert abs(path[-1][1] - end[1]) < 1e-2

def test_overshoot_trajectory():
    start = (50.0, 50.0)
    end = (300.0, 300.0)
    path = generate_overshoot_trajectory(start, end)
    assert len(path) > 30
    # Last point must arrive accurately at end target
    assert abs(path[-1][0] - end[0]) < 1.0
    assert abs(path[-1][1] - end[1]) < 1.0
