import sys

import pytest
from unittest import mock
from typing import List

from ci.ray_ci.container import run_script_in_docker, run_tests


class MockPopen:
    """
    Mock subprocess.Popen. This process returns 0 if both test_targets and
    commands are not empty; otherwise return 1.
    """

    def __init__(self, test_targets: List[str]):
        self.test_targets = test_targets

    def wait(self) -> int:
        return 0 if self.test_targets else 1


def test_run_script_in_docker() -> None:
    def _mock_check_output(input: List[str]) -> None:
        input_str = " ".join(input)
        assert "/bin/bash -ice run command" in input_str

    with mock.patch("subprocess.check_output", side_effect=_mock_check_output):
        run_script_in_docker("run command")


def test_run_tests() -> None:
    def _mock_run_tests_in_docker(test_targets: List[str]) -> MockPopen:
        return MockPopen(test_targets)

    with mock.patch(
        "ci.ray_ci.container._run_tests_in_docker",
        side_effect=_mock_run_tests_in_docker,
    ), mock.patch(
        "ci.ray_ci.container._setup_test_environment",
        return_value=None,
    ):
        # test_targets are not empty
        assert run_tests("team", ["t1", "t2"], 2)
        # test_targets is empty after chunking
        assert not run_tests("team", ["t1"], 2)


if __name__ == "__main__":
    sys.exit(pytest.main(["-v", __file__]))
