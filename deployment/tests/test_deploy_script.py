import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "scenario,message",
    [
        ("ok", "staged and its deployed settings match"),
        ("wrong-tag", "Candidate tag changed"),
        ("split-traffic", "Inference production traffic mismatch"),
        ("timeout-drift", "Inference request timeout mismatch"),
    ],
)
def test_actual_deploy_script_with_mocked_cloud(scenario, message):
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        pytest.skip("PowerShell is needed for the deployment script contract test")
    result = subprocess.run(
        [
            shell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(Path(__file__).with_name("deploy-clap-fixture.ps1")),
            scenario,
        ],
        capture_output=True,
        timeout=30,
    )
    output = (result.stdout + result.stderr).decode("utf-8", errors="replace")
    assert (result.returncode == 0) == (scenario == "ok"), output
    assert message in output, output
