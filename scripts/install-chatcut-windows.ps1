# === Install ChatCut plugin untuk Claude Code (Windows) ===
# Jalankan di PowerShell (bukan CMD). Jika dijalankan sebagai file .ps1:
#   powershell -ExecutionPolicy Bypass -File .\install-chatcut-windows.ps1
$ErrorActionPreference = 'Continue'

# 1) Pastikan Claude Code CLI ada & mendukung 'claude mcp login'
if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
  irm https://claude.ai/install.ps1 | iex
  $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
claude mcp login --help *> $null
if ($LASTEXITCODE -ne 0) {
  claude update
  claude mcp login --help *> $null
  if ($LASTEXITCODE -ne 0 -and (Get-Command npm -ErrorAction SilentlyContinue)) {
    npm install -g @anthropic-ai/claude-code@latest
  }
}
claude --version

# 2) Pastikan Git terpasang
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  winget install --id Git.Git -e --silent --accept-source-agreements --accept-package-agreements
  $env:Path += ";$env:ProgramFiles\Git\cmd"
}

# 3) Tambah marketplace ChatCut (BISA BEBERAPA MENIT - jangan dibatalkan walau diam)
claude plugin marketplace add https://github.com/ChatCut-Inc/agent-plugin.git#main
if ($LASTEXITCODE -ne 0) {
  Write-Host "Clone penuh gagal, mencoba shallow clone..." -ForegroundColor Yellow
  $dir = "$env:USERPROFILE\chatcut-agent-plugin"
  if (Test-Path $dir) { Remove-Item $dir -Recurse -Force }
  git clone --depth 1 --branch main https://github.com/ChatCut-Inc/agent-plugin.git $dir
  claude plugin marketplace add $dir
}

# 4) Install plugin
claude plugin install chatcut@chatcut-inc

# 5) Hapus server lama bernama persis 'chatcut' (JANGAN hapus plugin:chatcut:chatcut)
claude mcp get chatcut *> $null
if ($LASTEXITCODE -eq 0) { claude mcp remove chatcut }

# 6) Login - browser terbuka, klik Authorize
claude mcp login plugin:chatcut:chatcut

# 7) Verifikasi
claude plugin list
claude mcp get plugin:chatcut:chatcut
