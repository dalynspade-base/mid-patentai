# 使用为新颖性 Harness 准备的环境；不依赖原来失效的 .venv。
$ErrorActionPreference = 'Stop'
$noveltyPython = Join-Path $PSScriptRoot '.venv-novelty\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $noveltyPython)) {
    throw '缺少 .venv-novelty 运行环境，请按 README 的“安装依赖”步骤创建环境。'
}
Set-Location -LiteralPath $PSScriptRoot
& $noveltyPython -X utf8 -m streamlit run (Join-Path $PSScriptRoot 'app.py') --server.address 127.0.0.1
