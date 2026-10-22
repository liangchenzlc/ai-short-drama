"""向定向测试入口报告执行数量；不记录配置、请求或凭据。"""

import json
import os
from pathlib import Path

import pytest


class Results:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0
        self.skipped = 0

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        if report.skipped:
            self.skipped += 1
        elif report.failed:
            self.failed += 1
        elif report.when == "call" and report.passed:
            self.passed += 1

    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        target = os.environ.get("SHORT_DRAMA_TEST_REPORT")
        if target:
            path = Path(target)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "collected": session.testscollected,
                        "passed": self.passed,
                        "failed": self.failed,
                        "skipped": self.skipped,
                        "exit_code": int(exitstatus),
                    }
                )
                + "\n",
                encoding="utf-8",
            )


def pytest_configure(config: pytest.Config) -> None:
    config.pluginmanager.register(Results(), "short-drama-test-results")
