<#
.SYNOPSIS
    Coleta do ImpactoCidadão: leis/MPs (Câmara e Senado) e planos de governo (TSE).

.DESCRIPTION
    Pensado para rodar sozinho pelo Agendador de Tarefas (veja scripts\agendar.ps1), mas funciona à mão.
    1) garante que o Docker e o banco estejam no ar (abre o Docker Desktop se preciso);
    2) roda coletor.py e coletor_candidatos.py;
    3) grava tudo em logs\coleta-AAAA-MM-DD.log (UTF-8).

    A tradução por IA só acontece se houver chave no .env (modo --ia auto dos coletores);
    sem chave, os atos são coletados e ficam "pendentes" para quando a chave existir.

.PARAMETER Dias
    Janela de apresentação das leis/MPs (padrão 30). Atos já conhecidos e ainda em tramitação
    continuam sendo atualizados mesmo fora dela.
#>
param([int]$Dias = 30)

$ErrorActionPreference = "Continue"
$raiz = Split-Path -Parent $PSScriptRoot
Set-Location $raiz

$pastaLogs = Join-Path $raiz "logs"
New-Item -ItemType Directory -Force $pastaLogs | Out-Null
$log = Join-Path $pastaLogs ("coleta-{0}.log" -f (Get-Date -Format "yyyy-MM-dd"))

function Registrar([string]$mensagem) {
    Add-Content -Path $log -Value ("{0} {1}" -f (Get-Date -Format "HH:mm:ss"), $mensagem) -Encoding UTF8
}

# Python do ambiente virtual (fora do OneDrive). Sobrescreva com a variável IC_PYTHON se precisar.
$py = if ($env:IC_PYTHON) { $env:IC_PYTHON } else { Join-Path $env:USERPROFILE ".venvs\impacto\Scripts\python.exe" }
if (-not (Test-Path $py)) { Registrar "ERRO: Python do venv não encontrado em $py (defina IC_PYTHON)."; exit 2 }
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUNBUFFERED = "1"

# Executa o Python capturando stdout/stderr em arquivos (evita o ruído "NativeCommandError" do
# PowerShell 5.1 e os acentos quebrados) e anexa tudo ao log em UTF-8. Devolve o código de saída.
function Executar-Python([string[]]$argumentos) {
    $saida = [IO.Path]::GetTempFileName()
    $erro = [IO.Path]::GetTempFileName()
    $proc = Start-Process -FilePath $py -ArgumentList $argumentos -NoNewWindow -Wait -PassThru `
        -RedirectStandardOutput $saida -RedirectStandardError $erro
    foreach ($arquivo in $saida, $erro) {
        Get-Content -Path $arquivo -Encoding UTF8 | Add-Content -Path $log -Encoding UTF8
        [IO.File]::Delete($arquivo)
    }
    return $proc.ExitCode
}

function Motor-Docker-Ativo {
    $versao = docker info --format '{{.ServerVersion}}' 2>$null
    return ($LASTEXITCODE -eq 0 -and [bool]$versao)
}

Registrar "=== Início da coleta (janela: $Dias dias) ==="

# 1) Docker + banco
if (-not (Motor-Docker-Ativo)) {
    Registrar "Docker parado; abrindo o Docker Desktop..."
    $exe = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    if (Test-Path $exe) { Start-Process $exe }
    for ($i = 0; $i -lt 36 -and -not (Motor-Docker-Ativo); $i++) { Start-Sleep -Seconds 5 }
}
if (-not (Motor-Docker-Ativo)) { Registrar "ERRO: o Docker não ficou disponível; coleta cancelada."; exit 3 }

docker compose up -d 2>&1 | Out-Null
for ($i = 0; $i -lt 30; $i++) {
    if ((docker inspect -f '{{.State.Health.Status}}' impacto-db 2>$null) -eq "healthy") { break }
    Start-Sleep -Seconds 2
}

# 2) Coletas (independentes: uma falha não impede a outra)
Registrar "--- leis e medidas provisórias ---"
$codigoLeis = Executar-Python @("-u", "backend\coletor.py", "--dias", "$Dias")
Registrar "coletor.py terminou com código $codigoLeis"

Registrar "--- planos de governo ---"
$codigoPlanos = Executar-Python @("-u", "backend\coletor_candidatos.py")
Registrar "coletor_candidatos.py terminou com código $codigoPlanos"

Registrar "=== Fim da coleta ==="
if ($codigoLeis -ne 0 -or $codigoPlanos -ne 0) { exit 1 }
exit 0
