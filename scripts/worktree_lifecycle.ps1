[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [ValidateSet('Audit', 'VerifyCloseout', 'Remove')]
    [string]$Action = 'Audit',

    [string]$Repository = '',

    [string]$Path,

    [switch]$RefreshRemote,

    [switch]$RequireOnlyMain,

    [switch]$ShowAllFiles,

    # Audit 配套：存在未收尾工作树时以 exit 2 结束（默认恒 exit 0 保持兼容）
    [switch]$StrictExit,

    [switch]$Apply,

    [switch]$ConfirmInactive,

    [switch]$DeleteBranch,

    # 配合 DeleteBranch：当通过 PR 确证已 Squash Merge 到 main 且 head 匹配时，允许安全清理本地分支
    [switch]$AllowSquashDelete
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Invoke-Git {
    param(
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    $output = @(& git -C $WorkingDirectory @Arguments 2>&1)
    $exitCode = $LASTEXITCODE
    $lines = @($output | ForEach-Object { [string]$_ })
    return [pscustomobject]@{
        ExitCode = $exitCode
        Lines    = $lines
        Text     = [string]::Join([Environment]::NewLine, $lines)
    }
}

function Get-GitValue {
    param(
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    $result = Invoke-Git -WorkingDirectory $WorkingDirectory -Arguments $Arguments
    if ($result.ExitCode -ne 0) {
        throw "git $($Arguments -join ' ') 执行失败：$($result.Text)"
    }
    return $result.Text.Trim()
}

function ConvertTo-NormalizedPath {
    param([Parameter(Mandatory = $true)][string]$Value)

    $fullPath = [IO.Path]::GetFullPath($Value)
    return $fullPath.TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
}

function Get-WorktreeRecords {
    param([Parameter(Mandatory = $true)][string]$RepositoryRoot)

    $result = Invoke-Git -WorkingDirectory $RepositoryRoot -Arguments @('worktree', 'list', '--porcelain')
    if ($result.ExitCode -ne 0) {
        throw "读取 Git 工作树失败：$($result.Text)"
    }

    $records = [System.Collections.Generic.List[object]]::new()
    $blocks = [regex]::Split($result.Text.Trim(), '(?:\r?\n){2,}')
    foreach ($block in $blocks) {
        if ([string]::IsNullOrWhiteSpace($block)) {
            continue
        }

        $record = [ordered]@{
            Path     = $null
            Head     = $null
            Branch   = $null
            Detached = $false
            Locked   = $false
            Prunable = $false
        }
        foreach ($line in ($block -split '\r?\n')) {
            if ($line -match '^worktree (.+)$') {
                $record.Path = ConvertTo-NormalizedPath -Value $Matches[1]
            } elseif ($line -match '^HEAD (.+)$') {
                $record.Head = $Matches[1]
            } elseif ($line -match '^branch refs/heads/(.+)$') {
                $record.Branch = $Matches[1]
            } elseif ($line -eq 'detached') {
                $record.Detached = $true
            } elseif ($line -match '^locked(?: .*)?$') {
                $record.Locked = $true
            } elseif ($line -match '^prunable(?: .*)?$') {
                $record.Prunable = $true
            }
        }

        if ($record.Path) {
            $records.Add([pscustomobject]$record)
        }
    }
    return @($records)
}

function Get-WorktreeStatus {
    param([Parameter(Mandatory = $true)][string]$WorktreePath)

    if (-not (Test-Path -LiteralPath $WorktreePath -PathType Container)) {
        return [pscustomobject]@{
            Error     = '工作树目录不存在'
            Modified  = @()
            Untracked = @()
            Ignored   = @()
        }
    }

    $result = Invoke-Git -WorkingDirectory $WorktreePath -Arguments @(
        'status', '--porcelain=v1', '--untracked-files=all', '--ignored=matching'
    )
    if ($result.ExitCode -ne 0) {
        return [pscustomobject]@{
            Error     = $result.Text
            Modified  = @()
            Untracked = @()
            Ignored   = @()
        }
    }

    $modified = [System.Collections.Generic.List[string]]::new()
    $untracked = [System.Collections.Generic.List[string]]::new()
    $ignored = [System.Collections.Generic.List[string]]::new()
    foreach ($line in $result.Lines) {
        if ($line.Length -lt 3) {
            continue
        }
        $code = $line.Substring(0, 2)
        $relativePath = $line.Substring(3)
        if ($code -eq '??') {
            $untracked.Add($relativePath)
        } elseif ($code -eq '!!') {
            $ignored.Add($relativePath)
        } else {
            $modified.Add($relativePath)
        }
    }

    return [pscustomobject]@{
        Error     = $null
        Modified  = @($modified)
        Untracked = @($untracked)
        Ignored   = @($ignored)
    }
}

function Test-CommitMerged {
    param(
        [Parameter(Mandatory = $true)][string]$RepositoryRoot,
        [Parameter(Mandatory = $true)][object]$Worktree,
        [Parameter(Mandatory = $true)][string]$OriginMain
    )

    $ancestor = Invoke-Git -WorkingDirectory $RepositoryRoot -Arguments @(
        'merge-base', '--is-ancestor', $Worktree.Head, $OriginMain
    )
    if ($ancestor.ExitCode -eq 0) {
        return [pscustomobject]@{ Merged = $true; ByAncestor = $true; Evidence = 'HEAD 已包含在 origin/main 历史中' }
    }

    if ($Worktree.Branch -and (Get-Command gh -ErrorAction SilentlyContinue)) {
        Push-Location $RepositoryRoot
        try {
            $ghOutput = @(& gh pr list --state merged --head $Worktree.Branch --json number,headRefName,headRefOid,baseRefName,mergedAt 2>&1)
            $ghExitCode = $LASTEXITCODE
        } finally {
            Pop-Location
        }

        if ($ghExitCode -eq 0) {
            try {
                $pullRequests = @($ghOutput | ForEach-Object { [string]$_ } | ConvertFrom-Json)
                $matchingPullRequest = $pullRequests | Where-Object {
                    $_.headRefName -eq $Worktree.Branch -and
                    $_.headRefOid -eq $Worktree.Head -and
                    $_.baseRefName -eq 'main' -and
                    $_.mergedAt
                } | Select-Object -First 1
                if ($matchingPullRequest) {
                    return [pscustomobject]@{
                        Merged    = $true
                        ByAncestor = $false
                        Evidence  = "PR #$($matchingPullRequest.number) 已合并到 main，且 PR 头提交与工作树 HEAD 一致"
                    }
                }
            } catch {
                # GitHub CLI 输出无法解析时继续按“未能证明已合并”处理。
            }
        }
    }

    return [pscustomobject]@{
        Merged     = $false
        ByAncestor = $false
        Evidence   = '无法证明该提交已进入 origin/main；若 PR 使用 squash/rebase，请先确认 gh 已登录并且 PR 头提交未变化'
    }
}

function Get-Inventory {
    param(
        [Parameter(Mandatory = $true)][string]$RepositoryRoot,
        [Parameter(Mandatory = $true)][string]$OriginMain
    )

    $rows = [System.Collections.Generic.List[object]]::new()
    foreach ($worktree in (Get-WorktreeRecords -RepositoryRoot $RepositoryRoot)) {
        $status = Get-WorktreeStatus -WorktreePath $worktree.Path
        $mergeProof = [pscustomobject]@{ Merged = $false; ByAncestor = $false; Evidence = '未检查' }

        if ($worktree.Branch -eq 'main') {
            $state = 'MAIN'
        } elseif ($worktree.Locked) {
            $state = 'LOCKED'
        } elseif ($status.Error) {
            $state = 'STATUS_ERROR'
        } elseif ($status.Modified.Count -gt 0 -or $status.Untracked.Count -gt 0 -or $status.Ignored.Count -gt 0) {
            $state = 'DIRTY_OR_IGNORED'
        } elseif ($OriginMain) {
            $mergeProof = Test-CommitMerged -RepositoryRoot $RepositoryRoot -Worktree $worktree -OriginMain $OriginMain
            if ($mergeProof.Merged) {
                $state = 'MERGED_CLEAN'
            } else {
                $state = 'NOT_PROVEN_MERGED'
            }
        } else {
            $state = 'REMOTE_REF_MISSING'
        }

        $rows.Add([pscustomobject]@{
            State      = $state
            Path       = $worktree.Path
            Branch     = if ($worktree.Branch) { $worktree.Branch } else { '(detached)' }
            Head       = if ($worktree.Head) { $worktree.Head.Substring(0, [Math]::Min(12, $worktree.Head.Length)) } else { '(unknown)' }
            Modified   = @($status.Modified)
            Untracked  = @($status.Untracked)
            Ignored    = @($status.Ignored)
            StatusError = $status.Error
            Locked     = $worktree.Locked
            Prunable   = $worktree.Prunable
            MergeProof = $mergeProof
            Raw        = $worktree
        })
    }
    return @($rows)
}

function Write-Inventory {
    param(
        [Parameter(Mandatory = $true)][object[]]$Rows,
        [bool]$IncludeAllFiles = $false
    )

    Write-Output '工作树状态（DIRTY_OR_IGNORED 表示有内容必须先检查/保留；不会自动清理）:'
    foreach ($row in $Rows) {
        Write-Output ("[{0}] branch={1} head={2} modified={3} untracked={4} ignored={5} path={6}" -f `
            $row.State, $row.Branch, $row.Head, $row.Modified.Count, $row.Untracked.Count, $row.Ignored.Count, $row.Path)
        foreach ($kind in @('Modified', 'Untracked', 'Ignored')) {
            $files = @($row.$kind)
            $limit = if ($IncludeAllFiles) { $files.Count } elseif ($kind -eq 'Ignored') { 10 } else { 25 }
            $filesToShow = @($files | Select-Object -First $limit)
            foreach ($file in $filesToShow) {
                Write-Output ("  {0}: {1}" -f $kind.ToLowerInvariant(), $file)
            }
            if (-not $IncludeAllFiles -and $files.Count -gt $filesToShow.Count) {
                Write-Output ("  {0}: 仅显示前 {1} 项；总计 {2} 项，使用 -ShowAllFiles 查看全部。" -f `
                    $kind.ToLowerInvariant(), $filesToShow.Count, $files.Count)
            }
        }
        if ($row.MergeProof.Evidence -ne '未检查') {
            Write-Output ("  merge: {0}" -f $row.MergeProof.Evidence)
        }
        if ($row.StatusError) {
            Write-Output ("  status-error: {0}" -f $row.StatusError)
        }
    }
}

function Invoke-RemoteFetch {
    param([Parameter(Mandatory = $true)][string]$RepositoryRoot)

    $fetch = Invoke-Git -WorkingDirectory $RepositoryRoot -Arguments @('fetch', 'origin')
    if ($fetch.ExitCode -ne 0) {
        throw "无法更新 origin 远端引用，不能证明合并状态：$($fetch.Text)"
    }
}

function Invoke-VerifyCloseout {
    param(
        [Parameter(Mandatory = $true)][string]$RepositoryRoot,
        [Parameter(Mandatory = $true)][object[]]$Rows,
        [string]$ExpectedRemovedPath,
        [bool]$OnlyMain = $false
    )

    $checksPassed = $true
    $messages = [System.Collections.Generic.List[string]]::new()
    $mainTree = $Rows | Where-Object { $_.Raw.Branch -eq 'main' } | Select-Object -First 1
    if (-not $mainTree) {
        $messages.Add('BLOCKED: 没有登记在 Git 中的 main 工作树。')
        $checksPassed = $false
    } else {
        if ($mainTree.Modified.Count -gt 0 -or $mainTree.Untracked.Count -gt 0 -or $mainTree.StatusError) {
            $messages.Add('BLOCKED: main 有未提交或无法读取的状态。')
            $checksPassed = $false
        } else {
            $messages.Add('PASS: main 的跟踪文件和未跟踪文件干净。')
        }

        $localMain = Get-GitValue -WorkingDirectory $RepositoryRoot -Arguments @('rev-parse', 'refs/heads/main')
        $originMain = Get-GitValue -WorkingDirectory $RepositoryRoot -Arguments @('rev-parse', 'refs/remotes/origin/main')
        if ($localMain -ne $originMain) {
            $messages.Add(("BLOCKED: main={0}，origin/main={1}，两者尚未对齐。" -f $localMain, $originMain))
            $checksPassed = $false
        } else {
            $messages.Add(("PASS: main 与 origin/main 一致（{0}）。" -f $localMain))
        }
    }

    if ($ExpectedRemovedPath) {
        $normalizedExpectedPath = ConvertTo-NormalizedPath -Value $ExpectedRemovedPath
        $stillRegistered = $Rows | Where-Object { $_.Path -eq $normalizedExpectedPath } | Select-Object -First 1
        if ($stillRegistered -or (Test-Path -LiteralPath $normalizedExpectedPath)) {
            $messages.Add(("BLOCKED: 任务工作树仍存在或仍登记：{0}" -f $normalizedExpectedPath))
            $checksPassed = $false
        } else {
            $messages.Add(("PASS: 任务工作树已移除：{0}" -f $normalizedExpectedPath))
        }
    }

    if ($OnlyMain) {
        $otherTrees = @($Rows | Where-Object { $_.Raw.Branch -ne 'main' })
        if ($otherTrees.Count -gt 0) {
            $messages.Add(("BLOCKED: 仍有 {0} 个非 main 工作树。只有确认均已结束并处理改动后，才能要求只留 main。" -f $otherTrees.Count))
            $checksPassed = $false
        } else {
            $messages.Add('PASS: 仓库只登记了 main 工作树。')
        }
    }

    return [pscustomobject]@{ Passed = $checksPassed; Messages = @($messages) }
}

try {
    # PS 5.1 下 param 默认值里 $PSScriptRoot 为空，改在主体内回退
    if ([string]::IsNullOrWhiteSpace($Repository)) {
        $Repository = (Join-Path $PSScriptRoot '..')
    }
    $resolvedRepository = (Resolve-Path -LiteralPath $Repository).Path
    $repositoryRoot = Get-GitValue -WorkingDirectory $resolvedRepository -Arguments @('rev-parse', '--show-toplevel')
    $repositoryRoot = ConvertTo-NormalizedPath -Value $repositoryRoot

    if ($Action -eq 'Audit' -and $RefreshRemote) {
        Invoke-RemoteFetch -RepositoryRoot $repositoryRoot
    } elseif ($Action -ne 'Audit') {
        Invoke-RemoteFetch -RepositoryRoot $repositoryRoot
    }

    $originMainResult = Invoke-Git -WorkingDirectory $repositoryRoot -Arguments @('rev-parse', '--verify', 'refs/remotes/origin/main')
    $originMain = if ($originMainResult.ExitCode -eq 0) { $originMainResult.Text.Trim() } else { $null }
    $rows = Get-Inventory -RepositoryRoot $repositoryRoot -OriginMain $originMain

    if ($Action -eq 'Audit') {
        Write-Inventory -Rows $rows -IncludeAllFiles $ShowAllFiles.IsPresent
        # #486：Audit 默认 informational（恒 exit 0，兼容既有调用方）；
        # -StrictExit 时存在未收尾树（仍有改动/未证明合并）以非零退出码暴露，
        # 供 CI/脚本判断，与 VerifyCloseout 的失败码对齐。
        if ($StrictExit.IsPresent) {
            $unclean = @($rows | Where-Object { $_.State -notin @('MAIN', 'MERGED_CLEAN') })
            if ($unclean.Count -gt 0) {
                Write-Output ("STRICT: 发现 {0} 个未收尾工作树（exit 2）。" -f $unclean.Count)
                exit 2
            }
        }
        exit 0
    }

    if ($Action -eq 'VerifyCloseout') {
        $verification = Invoke-VerifyCloseout -RepositoryRoot $repositoryRoot -Rows $rows `
            -ExpectedRemovedPath $Path -OnlyMain $RequireOnlyMain.IsPresent
        foreach ($message in $verification.Messages) {
            Write-Output $message
        }
        if (-not $verification.Passed) {
            exit 2
        }
        Write-Output 'PASS: 收尾检查通过。'
        exit 0
    }

    if (-not $Path) {
        throw 'Remove 操作必须提供 -Path。'
    }
    if (-not $Apply) {
        throw 'Remove 默认只检查；执行删除必须显式提供 -Apply。'
    }
    if (-not $ConfirmInactive) {
        throw '脚本无法判断另一个会话是否仍在使用工作树；确认已无人使用后，必须显式提供 -ConfirmInactive。'
    }

    $targetPath = ConvertTo-NormalizedPath -Value $Path
    $target = $rows | Where-Object { $_.Path -eq $targetPath } | Select-Object -First 1
    if (-not $target) {
        throw "指定路径不是当前仓库登记的工作树：$targetPath"
    }
    if ($target.Raw.Branch -eq 'main' -or $target.Path -eq $repositoryRoot) {
        throw '禁止通过此脚本删除 main 工作树。'
    }
    if ($target.Locked) {
        throw '该工作树已锁定；脚本不会绕过锁定状态。'
    }
    if ($target.StatusError) {
        throw "无法读取工作树状态：$($target.StatusError)"
    }
    if ($target.Modified.Count -gt 0 -or $target.Untracked.Count -gt 0 -or $target.Ignored.Count -gt 0) {
        throw ("工作树还有内容，拒绝删除（修改 {0}、未跟踪 {1}、忽略 {2}）。先归档或妥善处理，再重新检查。" -f `
            $target.Modified.Count, $target.Untracked.Count, $target.Ignored.Count)
    }
    if (-not $target.MergeProof.Merged) {
        throw "不能证明提交已合并到 origin/main，拒绝删除：$($target.MergeProof.Evidence)"
    }

    $currentPath = ConvertTo-NormalizedPath -Value (Get-Location).ProviderPath
    if ($currentPath -eq $target.Path -or $currentPath.StartsWith($target.Path + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw '当前终端正在该工作树目录中；请先切换到其他目录再执行删除。'
    }

    $forceDelete = $false
    if ($DeleteBranch -and $target.Raw.Branch -and -not $target.MergeProof.ByAncestor) {
        if ($AllowSquashDelete.IsPresent -and $target.MergeProof.ByPR -and $target.MergeProof.Merged) {
            $forceDelete = $true
        } else {
            throw '已确认 PR 合并，但分支 HEAD 不是 origin/main 的祖先（常见于 squash merge）；为避免强删，本次只允许保留本地分支并移除工作树（若确认已合入 PR，可追加 -AllowSquashDelete 开关清理本地分支）。'
        }
    }

    # 2026-11-21：不用 $PSCmdlet.ShouldProcess —— ConfirmImpact='High' 会让它
    # 在 ConfirmPreference=High（默认）下必然渲染交互确认，而以 -File 从非交互
    # 宿主（Git Bash 管道/CI）调用时 Host 无 UI，ShouldProcess 内部抛
    # NullReferenceException（PowerShell 5.1 实测，最小复现确认 -Confirm:$false
    # 可绕过）。本脚本的删除确认已由 -Apply + -ConfirmInactive 双开关显式承担，
    # -WhatIf 语义改用 $WhatIfPreference 保留，不再依赖宿主交互能力。
    if ($WhatIfPreference) {
        Write-Output "WHATIF: 将移除工作树 $($target.Path)（检查通过；去掉 -WhatIf 并加 -Apply -ConfirmInactive 执行）。"
        exit 0
    }

    $remove = Invoke-Git -WorkingDirectory $repositoryRoot -Arguments @('worktree', 'remove', $target.Path)
    if ($remove.ExitCode -ne 0) {
        throw "Git 拒绝移除工作树：$($remove.Text)"
    }

    if ($DeleteBranch -and $target.Raw.Branch) {
        $branchDeleteArg = if ($forceDelete) { '-D' } else { '-d' }
        $delete = Invoke-Git -WorkingDirectory $repositoryRoot -Arguments @('branch', $branchDeleteArg, $target.Raw.Branch)
        if ($delete.ExitCode -ne 0) {
            throw "工作树已移除，但 Git 未能安全删除本地分支；分支已保留：$($delete.Text)"
        }
        Write-Output ("PASS: 已安全删除本地分支：{0}" -f $target.Raw.Branch)
    }

    if (Test-Path -LiteralPath $target.Path) {
        throw 'Git 命令返回成功，但工作树目录仍存在；请检查，不要强制清理。'
    }
    Write-Output ("PASS: 已移除工作树：{0}" -f $target.Path)
    if ($target.Raw.Branch -and -not $DeleteBranch) {
        Write-Output ("INFO: 本地分支仍保留：{0}。需要时可在确认安全后单独删除。" -f $target.Raw.Branch)
    }
    exit 0
} catch {
    Write-Output ("BLOCKED: {0}" -f $_.Exception.Message)
    exit 2
}
