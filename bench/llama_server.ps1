# Start llama.cpp's llama-server on 127.0.0.1:8090 for LOCAL_ENGINE=llamacpp.
# Needs: winget install ggml.llamacpp, and an upstream GGUF in models\ (Ollama's own Qwen 3.5 blob
# does not load in llama.cpp). Default model: models\Qwen3.5-4B-Q4_K_M.gguf from unsloth/Qwen3.5-4B-GGUF.
param([string]$Model = "$PSScriptRoot\..\models\Qwen3.5-4B-Q4_K_M.gguf", [int]$Context = 65536)
$exe = (Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\ggml.llamacpp*" -Recurse -Filter llama-server.exe |
        Select-Object -First 1).FullName
if (-not $exe) { throw 'llama-server not found: winget install ggml.llamacpp' }
if (-not (Test-Path $Model)) { throw "Model not found: $Model" }
# --host 127.0.0.1 only: Docker reaches it as host.docker.internal; never exposed to the tailnet.
& $exe -m $Model --host 127.0.0.1 --port 8090 -c $Context -ngl 99 --jinja --alias qwen3.5-4b
