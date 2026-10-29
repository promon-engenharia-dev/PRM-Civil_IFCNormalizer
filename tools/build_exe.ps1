# Gera o executável Windows em dist\NormalizadorIFC\ (pasta autocontida, sem Python na máquina).
# Uso (na raiz do repositório):  powershell -ExecutionPolicy Bypass -File tools\build_exe.ps1
param([string]$Destino = "dist")

$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $PSScriptRoot
Set-Location $raiz
$py = Join-Path $raiz ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Ambiente .venv não encontrado. Veja README (Instalação)." }

& $py -m pip install -q -e ".[dev]"
if (-not $?) { throw "Falha ao instalar dependências." }

$ui = Join-Path $raiz "src\ifc_normalizer\gui\ui"
# Arquivos intermediários fora do OneDrive: a sincronização trava a pasta e o --clean falha.
$trabalho = Join-Path $env:TEMP "normalizador-ifc-build"
# A remoção interna do PyInstaller falha em pastas sincronizadas pelo OneDrive; o PowerShell não.
$anterior = Join-Path $raiz "$Destino\NormalizadorIFC"
if (Test-Path $anterior) { Remove-Item -Recurse -Force $anterior }
& $py -m PyInstaller --noconfirm --clean --onedir --windowed `
    --name "NormalizadorIFC" `
    --collect-all ifcopenshell `
    --collect-all webview `
    --collect-submodules ifc_normalizer `
    --add-data "$ui;ifc_normalizer\gui\ui" `
    --distpath (Join-Path $raiz $Destino) `
    --workpath (Join-Path $trabalho "work") `
    --specpath $trabalho `
    (Join-Path $raiz "tools\entry_gui.py")
if (-not $?) { throw "Falha no PyInstaller." }

# Regras ficam ao lado do .exe para a equipe editar sem recompilar.
$app = Join-Path $raiz "$Destino\NormalizadorIFC"
Copy-Item -Recurse -Force (Join-Path $raiz "rules") (Join-Path $app "rules")
# Bases de referência usadas na verificação (catálogo bSI e conhecimento do IFA da NIST).
New-Item -ItemType Directory -Force (Join-Path $app "ref") | Out-Null
foreach ($pasta in @("bsi-validacao", "nist-ifa")) {
    Copy-Item -Recurse -Force (Join-Path $raiz "ref\$pasta") (Join-Path $app "ref\$pasta")
}
Copy-Item -Force (Join-Path $raiz "docs\regras.md") (Join-Path $app "rules\LEIA-ME-regras.md")
Copy-Item -Force (Join-Path $raiz "tools\LEIA-ME.txt") (Join-Path $app "LEIA-ME.txt")

$tamanho = "{0:N0} MB" -f ((Get-ChildItem $app -Recurse | Measure-Object Length -Sum).Sum / 1MB)
Write-Host "Pronto: $app ($tamanho)"
