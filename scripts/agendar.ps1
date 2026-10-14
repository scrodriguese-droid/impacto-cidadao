<#
.SYNOPSIS
    Agenda (ou remove) a coleta diária do ImpactoCidadão no Agendador de Tarefas do Windows.

.EXAMPLE
    .\scripts\agendar.ps1                      # todo dia às 07:30, janela de 30 dias
    .\scripts\agendar.ps1 -Hora 06:00 -Dias 60
    .\scripts\agendar.ps1 -Remover

.NOTES
    * Roda com a sua conta, sem pedir senha e sem administrador; só executa com você logado.
    * "Executar assim que possível se perder o horário" está ligado: se o PC estava desligado às 07:30,
      a coleta roda quando ele voltar.
    * Para testar agora:  Start-ScheduledTask -TaskName ImpactoCidadao-Coleta
#>
param(
    [string]$Hora = "07:30",
    [int]$Dias = 30,
    [switch]$Remover
)

$nome = "ImpactoCidadao-Coleta"

if ($Remover) {
    if (Get-ScheduledTask -TaskName $nome -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $nome -Confirm:$false
        Write-Host "Tarefa '$nome' removida."
    } else {
        Write-Host "A tarefa '$nome' não existe."
    }
    return
}

$script = Join-Path $PSScriptRoot "coletar.ps1"
if (-not (Test-Path $script)) { throw "Não achei $script" }

$acao = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`" -Dias $Dias"
$gatilho = New-ScheduledTaskTrigger -Daily -At $Hora
$ajustes = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $nome -Action $acao -Trigger $gatilho -Settings $ajustes -Principal $principal `
    -Description "ImpactoCidadão: coleta diária de leis/MPs e planos de governo (veja logs\ na pasta do projeto)." `
    -Force | Out-Null

$t = Get-ScheduledTask -TaskName $nome
Write-Host "Tarefa '$nome' agendada: todo dia às $Hora, janela de $Dias dias. Estado: $($t.State)."
Write-Host "Log da coleta: $(Join-Path (Split-Path -Parent $PSScriptRoot) 'logs')"
