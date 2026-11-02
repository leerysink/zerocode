# zerocode 一键部署 —— Windows 侧的 WSL 皮（ADR-0035）
#
# 用法两条，选哪条都行，没有任何命令行参数：
#     双击同目录的 deploy.bat（它只做一件事：用下面的命令把本文件跑起来）
#     powershell -ExecutionPolicy Bypass -File .\deploy.ps1
#
# 这个文件只做 Windows 侧才有的三件事：保证 WSL2 可用、保证发行版里开了 systemd、
# 把交付包推进发行版，然后在发行版里执行**同一份** deploy.sh。部署本身的提问、环境保障、
# 升级护栏、起栈与自检全在 deploy.sh 里——皮里不留第二套判定，否则两边会各自漂移。
#
# 刻意不装 Docker Desktop（ADR-0035）：它的 license 与「docker CLI 指向谁」都会跟这条路打架，
# 而 deploy.sh 装的是 Docker Engine + compose v2 插件，正好是发行版里需要的东西。
#
# 本文件必须 UTF-8 带 BOM 且行尾 LF：Windows PowerShell 5.1 在 GBK 代码页下读无 BOM 的
# 中文脚本会解析失败（US-26）。打包器（scripts/build_output.py）按这条口径搬运。

$ErrorActionPreference = 'Stop'
# wsl.exe 默认吐 UTF-16LE，PowerShell 按控制台代码页解码会得到乱码；WSL_UTF8=1 让它走 UTF-8，
# 再把控制台编码也钉住，两处一起对齐才读得懂发行版里那句中文报错。
$env:WSL_UTF8 = '1'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

if ($args.Count -gt 0) {
    Write-Host '本脚本不接受命令行参数（已定决策 D-11，危险动作改为现场提问）。' -ForegroundColor Red
    Write-Host '  以前 flag 的去处都在 Linux 侧的 deploy.sh 里，不带参数重跑即可：双击 deploy.bat，或 powershell -File .\deploy.ps1' -ForegroundColor Red
    Write-Host '  只想在 Linux/WSL 里跑：bash deploy.sh（皮只是替你把包放进发行版）。' -ForegroundColor Red
    exit 2
}

function Say($m)  { Write-Host "`n▶ $m" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "  ✓ $m" }
function Warn($m) { Write-Host "  ! $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "`n✗ $m" -ForegroundColor Red; exit 1 }

function Ask($q, $d) {
    $tip = if ($d) { "［回车 = $d］" } else { '［可留空］' }
    $a = Read-Host "  $q $tip"
    if ([string]::IsNullOrWhiteSpace($a)) { return $d }
    return $a.Trim()
}

function AskYes($q, $d) {
    $a = Ask $q $d
    return ($a -match '^(y|Y|yes|YES)$')
}

# wsl.exe 的输出编码随 Windows 版本变，统一走这个函数：拿不到结果就抛，不猜。
function Wsl($distro, [string[]]$cmd, [switch]$Capture) {
    $argv = @('--')
    if ($distro) { $argv = @('-d', $distro) + $argv }
    $argv += $cmd
    if ($Capture) {
        $out = & wsl.exe @argv 2>&1
        if ($LASTEXITCODE -ne 0) { throw "wsl $($cmd -join ' ') 失败（退出码 $LASTEXITCODE）：$($out -join ' / ')" }
        return ($out | Out-String).Trim()
    }
    & wsl.exe @argv
    return $LASTEXITCODE
}

# ---------------------------------------------------------------- 1. WSL2 本体

Say '1/4 WSL2 就位'
$wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue
if (-not $wsl) {
    Fail '这台机器没有 wsl.exe：先在管理员 PowerShell 里跑 wsl --install，重启后再回来跑本脚本。'
}

# --status 在未装 WSL 组件时非零退出；这里不直接 wsl --install，先问，因为它要重启。
# 老一版 wsl.exe 没有 --status（会报 unrecognized option），所以判定只看退出码不看输出。
$statusOk = $true
$statusText = ''
try { $statusText = Wsl $null @('--status') -Capture } catch { $statusOk = $false }

if (-not $statusOk) {
    Warn '检测不到可用的 WSL 组件（wsl --status 没跑成）。'
    if (AskYes '现在启用 WSL 与虚拟机平台吗？需要管理员权限，之后必须重启（y/N）' 'n') {
        try {
            Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -NoRestart -ErrorAction Stop | Out-Null
            Enable-WindowsOptionalFeature -Online -FeatureName VirtualMachinePlatform -NoRestart -ErrorAction Stop | Out-Null
        } catch {
            Warn "启用可选组件失败：$($_.Exception.Message)"
            Warn '多半是这里不是管理员 PowerShell。用管理员身份开一个 PowerShell，跑 wsl --install，重启后再回来。'
            Fail '本脚本不代劳重启，也不在权限不够时硬试。'
        }
        try { $null = Wsl $null @('--set-default-version', '2') -Capture } catch { Warn '设默认版本 2 失败，重启后 wsl --install 会自己建 v2 发行版' }
        Say '组件已启用 —— 这一步要重启才生效'
        Write-Host '  现在**不要**继续跑。请：'
        Write-Host '    1) restart（开始菜单 → 电源 → 重新启动）'
        Write-Host '    2) 重启后把同一条 powershell -File .\deploy.ps1 再跑一次'
        Write-Host '  本脚本刻意不自动重启：正在跑的别的程序不该由部署来决定丢不丢（D-10）。'
        exit 0
    }
    Fail '没有 WSL2 就没办法在 Windows 上跑这套容器化部署（ADR-0035）。要么启用 WSL，要么直接在一台 Linux 机器上跑 bash deploy.sh。'
}
Ok 'wsl.exe 与 WSL 组件在位'
try { $null = Wsl $null @('--set-default-version', '2') -Capture } catch { Warn '设默认版本 2 没成功，继续用现有发行版' }

# ---------------------------------------------------------------- 2. 挑一个 v2 发行版

Say '2/4 选发行版'
# wsl -l -v 的表头随版本变（NAME/STATE/VERSION 或 名称/状态/版本），所以只认最后一列的数字。
$rows = @()
try { $rows = (Wsl $null @('-l', '-v') -Capture) -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ } } catch { }
$rows = $rows | Where-Object { $_ -notmatch '^(NAME|名称)' }
$distros = @()
foreach ($r in $rows) {
    if ($r -match '^(?<n>.+?)\s+(?<s>.+?)\s+(?<v>[12])$') {
        $distros += [pscustomobject]@{ Name = $Matches.n.Trim(); State = $Matches.s.Trim(); Version = [int]$Matches.v }
    }
}
if ($distros.Count -eq 0) {
    Warn '列不出任何发行版（wsl -l -v 没返回可解析的行）。'
    if (AskYes '现在装 Ubuntu-24.04 吗？（wsl --install -d Ubuntu-24.04，要从头下载，几分钟）(y/N)' 'n') {
        & wsl.exe --install -d Ubuntu-24.04 --no-launch
        if ($LASTEXITCODE -ne 0) {
            Warn "带 --no-launch 的装法退出码 $LASTEXITCODE（老版 wsl.exe 没这个选项），退回普通装法再试一次"
            & wsl.exe --install -d Ubuntu-24.04
            if ($LASTEXITCODE -ne 0) { Fail "wsl --install -d Ubuntu-24.04 退出码 $LASTEXITCODE：网络或商店源不通，先手工确认 wsl -l -v 能列出它" }
        }
        Say '发行版已装好，可能要重启一次才起得来'
        Write-Host '  把同一条 powershell -File .\deploy.ps1 再跑一次即可（已答项在部署根的 .deploy-answers 里）。'
        exit 0
    }
    Fail '没有可用的 WSL 发行版就没法继续。手工确认 wsl -l -v 至少列出一个 v2 发行版再重跑。'
}
$v2 = @($distros | Where-Object { $_.Version -eq 2 })
if ($v2.Count -eq 0) {
    $cand = $distros[0]
    Warn "只有 v1 发行版（$($cand.Name)）。本项目要 systemd 拉 docker daemon，v1 没有 systemd（规格 §7 的两处赌注之一）。"
    if (AskYes "现在把 $($cand.Name) 转成 v2 吗？（wsl --set-version $($cand.Name) 2，要迁移磁盘，几分钟到几十分钟）(y/N)" 'y') {
        & wsl.exe --set-version $cand.Name 2
        if ($LASTEXITCODE -ne 0) { Fail "转换失败，退出码 $LASTEXITCODE：先关掉占用该发行版的窗口再试" }
        Ok "已转成 v2：$($cand.Name)"
    } else {
        Fail 'v1 发行版里 docker daemon 起不来，部署会在环境保障那一步卡住。转 v2 或换一个 v2 发行版再来。'
    }
    $Distro = $cand.Name
} elseif ($v2.Count -eq 1) {
    $Distro = $v2[0].Name
    Ok "用唯一的一个 v2 发行版：$Distro"
} else {
    Write-Host ('  可选的 v2 发行版：' + (($v2 | ForEach-Object { $_.Name }) -join '、'))
    $Distro = Ask '用哪个发行版部署' $v2[0].Name
    if (@($v2 | Where-Object { $_.Name -eq $Distro }).Count -eq 0) { Fail "「$Distro」不在上面的列表里" }
}

# ---------------------------------------------------------------- 3. 发行版里的 systemd

Say '3/4 发行版内的 systemd'
$conf = ''
try { $conf = Wsl $Distro @('cat', '/etc/wsl.conf') -Capture } catch { Warn '/etc/wsl.conf 读不到（多半是还没有），按缺失处理' }
$hasSystemd = ($conf -match '(?m)^\s*systemd\s*=\s*true') -and ($conf -match '(?m)^\s*\[boot\]')
if ($hasSystemd) {
    Ok 'systemd 已开（docker daemon 靠它起来）'
} else {
    Warn '/etc/wsl.conf 里没有 [boot] systemd=true。deploy.sh 装的是 Docker Engine，daemon 要 systemd 拉；'
    Warn '没有它就一直卡在「连不上守护进程」那一环——这正是规格 §7 记下的一处未验证赌注。'
    if (AskYes '现在往发行版的 /etc/wsl.conf 追加这两行吗？（y/N）' 'y') {
        try {
            $null = Wsl $Distro @('bash', '-c', 'printf "[boot]\nsystemd=true\n" | sudo tee -a /etc/wsl.conf >/dev/null') -Capture
        } catch {
            Fail "写入失败：$($_.Exception.Message)。也可以在发行版里手工编辑 /etc/wsl.conf 加上 [boot] 与 systemd=true"
        }
        Ok '已追加 [boot] systemd=true'
        Say '配置要 wsl --shutdown 重启发行版才生效'
        Write-Host '  这一步不自动做：它会停掉该发行版里你正在跑的别的东西。请你手工执行：'
        Write-Host '    wsl --shutdown'
        Write-Host "    powershell -File .\deploy.ps1"
        Write-Host '  重跑时已答项会从部署根的 .deploy-answers 取默认值（US-07 / D-08）。'
        exit 0
    }
    Warn '没开 systemd 就继续：deploy.sh 的环境保障那一步会明确告诉你 daemon 起不来。'
    Warn '确认这台机器上没有别的程序在跑这个发行版时，可以现在执行 wsl --shutdown 让它生效（不重启也行不通）。'
}

# ---------------------------------------------------------------- 4. 包与执行

Say '4/4 把包放进发行版并跑 deploy.sh'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$bundle = $null
if ((Test-Path (Join-Path $here 'deploy.sh')) -and (Test-Path (Join-Path $here 'deploy_lib.sh'))) {
    $bundle = Ask '交付包目录（回车 = 本脚本所在目录，它看着就是一份包）' $here
} else {
    $bundle = Ask '交付包路径（.zip 或解开的目录）' (Join-Path $here 'build-output')
}
if ([string]::IsNullOrWhiteSpace($bundle)) { Fail '包路径不能空' }
$bundle = $bundle.Trim().Trim('"')
if (-not (Test-Path $bundle)) { Fail "找不到 $bundle" }

# zip：解到临时目录再走同一条路。解出来的包若被套了一层同名目录，往里钻一层。
if (Test-Path $bundle -PathType Leaf) {
    if ($bundle -notmatch '\.zip$') { Fail "只认 .zip 文件，收到的是 $bundle" }
    $work = Join-Path $env:TEMP ('zerocode-deploy-' + (Get-Date -Format 'yyyyMMddHHmmss'))
    New-Item -ItemType Directory -Force -Path $work | Out-Null
    Write-Host "  → 解压到 $work"
    Expand-Archive -LiteralPath $bundle -DestinationPath $work -Force
    $probe = Get-ChildItem -Path $work -Directory | Select-Object -First 1
    if ($probe -and -not (Test-Path (Join-Path $work 'deploy.sh')) -and (Test-Path (Join-Path $probe.FullName 'deploy.sh'))) {
        $work = $probe.FullName
    }
    $bundle = $work
}
foreach ($need in 'deploy.sh', 'deploy_lib.sh', 'README-deploy.md', '.env.example') {
    if (-not (Test-Path (Join-Path $bundle $need))) { Fail "包里没有 $need：$bundle（重跑 build-output.bat，别手往包里塞文件——它是生成目录）" }
}
# 皮与双击入口不进硬校验：老包本来就没有它们，缺了也只是少一个入口，而内核与判定层缺一都跑不动。
foreach ($entry in 'deploy.ps1', 'deploy.bat') {
    if (-not (Test-Path (Join-Path $bundle $entry))) { Warn "包里缺 $entry（旧包？）：内核与判定层都在，可以继续，但下次重打包会补上" }
}

$homeLinux = (Wsl $Distro @('bash', '-c', 'printf %s "$HOME"') -Capture).Trim()
if (-not $homeLinux) { Fail "问不到 $Distro 的 HOME" }
$targetLinux = "$homeLinux/zerocode-deploy"

# \\wsl.localhost（较新）与 \\wsl$（较旧）都试，选一个能用的provider。
$uncRoot = $null
foreach ($p in "\\wsl.localhost\$Distro", "\\wsl`$\$Distro") { if (Test-Path $p) { $uncRoot = $p; break } }
if (-not $uncRoot) { Fail "访问不了 $Distro 的文件系统（\\wsl.localhost 与 \\wsl`$ 都不通）。先跑一次 wsl -d $Distro 让它起来。" }
$unc = $uncRoot + '\' + ($homeLinux.TrimStart('/') -replace '/', '\') + '\zerocode-deploy'

Say '推进包（只覆盖不删：目标目录里可能有你上次跑出来的 .env 与 apps/）'
New-Item -ItemType Directory -Force -Path $unc | Out-Null
$src = (Get-Item -LiteralPath $bundle).FullName.TrimEnd('\')
$items = Get-ChildItem -LiteralPath $src -Force
if (-not $items) { Fail "包目录是空的：$src" }
foreach ($i in $items) {
    Write-Host "  → $($i.Name)"
    Copy-Item -LiteralPath $i.FullName -Destination $unc -Recurse -Force
}
Ok "包已推进 $targetLinux（$Distro）"
# 只覆盖不删是这条路的底线（目标目录里可能有你上次跑出来的 .env 与 apps/），代价是上一版包
# 里被删掉的脚本会留在这儿，而内核把 backend/ browser/ frontend/ 按 --delete 镜像进部署根时
# 会连它一起搬。想要一份全新的包：把它压成 .zip 再跑皮——解压到临时目录，天然全新。
Warn '推进只覆盖同名文件、不删多余的：上一版包留下的脚本会留在 ' + $targetLinux + '（想每次全新就走 .zip 入口）'

# Linux 侧脚本从 Windows 过去常带 CRLF——内核自己会自检并给修法，这里先替它剥掉更省事。
try {
    $null = Wsl $Distro @('bash', '-c', "cd '$targetLinux' && for f in deploy.sh deploy_lib.sh apply-init.sh; do [ -f `$f ] && sed -i 's/\r`$//' `$f; done") -Capture
} catch { Warn '剥 CRLF 这步没跑成（内核的自检会接手，症状出现时按它给的那条 sed 修）' }

Warn 'Windows 侧建议不起中央网关：宿主 80/443 常被占，通配 DNS 也指不到这台机器（ADR-0035 后果条）。'
Warn '内核会照旧问你一次，默认值已替你设成 n——坚持要起就答 y，不硬拦。'
Say "在 $Distro 里执行 bash deploy.sh（提问与输出都原样透传）"
$code = Wsl $Distro @('bash', '-c', "cd '$targetLinux' && ZC_GATEWAY_DEFAULT=n bash deploy.sh")
if ($code -ne 0) { Fail "deploy.sh 退出码 $code：按它停下的那一句处理，然后重跑本脚本（同一条命令，不用从头再来）" }
Ok '部署完成'
Write-Host '  控制台在 Linux 侧给出的地址上打开即可；WSL 里起的端口默认能从 Windows 的 localhost 访问。'
Write-Host "  再发一次版：双击 deploy.bat（或 powershell -File .\deploy.ps1），选「升级部署」，一路回车复用上次的答案。"
