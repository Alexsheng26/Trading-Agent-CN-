# Windows PowerShell 5.1 兼容性检查：解析每个脚本，标出 5.1 跑不了的写法。
# 由 test_windows_scripts.py 调用（需要 PowerShell 7 的 pwsh；GitHub 的 ubuntu-latest 自带）。
# 开发环境是 Linux，而用户机器上是 Win10/11 自带的 5.1：PowerShell 7 能跑不代表 5.1 能跑。
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Files)
$bad = 0
foreach ($file in $Files) {
    $bytes = [IO.File]::ReadAllBytes($file)
    if (-not ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)) {
        Write-Host "${file}: missing UTF-8 BOM"; $bad++
    }
    $tokens = $null; $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($file, [ref]$tokens, [ref]$errors)
    foreach ($e in $errors) { Write-Host "${file}:$($e.Extent.StartLineNumber): PARSE: $($e.Message)"; $bad++ }
    $flag = {
        param($node, $why)
        Write-Host "${file}:$($node.Extent.StartLineNumber): $why :: $($node.Extent.Text.Split("`n")[0])"
        $script:bad++
    }
    $all = $ast.FindAll({ $true }, $true)
    foreach ($n in $all) {
        $t = $n.GetType().Name
        if ($t -eq 'PipelineChainAst') { & $flag $n 'PS7-only && / ||' }
        elseif ($t -eq 'TernaryExpressionAst') { & $flag $n 'PS7-only ternary' }
        elseif ($t -eq 'BinaryExpressionAst' -and $n.Operator -eq 'QuestionQuestion') { & $flag $n 'PS7-only ??' }
        elseif ($t -eq 'AssignmentStatementAst' -and $n.Operator -eq 'QuestionQuestionEquals') { & $flag $n 'PS7-only ??=' }
        elseif (($t -eq 'MemberExpressionAst' -or $t -eq 'InvokeMemberExpressionAst' -or $t -eq 'IndexExpressionAst') -and $n.NullConditional) { & $flag $n 'PS7-only ?. / ?[]' }
        elseif ($t -eq 'VariableExpressionAst') {
            $name = $n.VariablePath.UserPath
            if ($name -match '[^\x00-\x7F]') { & $flag $n "variable name swallowed non-ASCII text: `$$name" }
            if ($name -in 'IsWindows','IsLinux','IsMacOS','IsCoreCLR') { & $flag $n "PS6+ automatic variable `$$name" }
        }
        elseif ($t -eq 'CommandAst') {
            $cmd = $n.GetCommandName()
            if ($cmd -eq 'Join-Path') {
                $positional = @($n.CommandElements | Select-Object -Skip 1 | Where-Object { $_ -isnot [System.Management.Automation.Language.CommandParameterAst] })
                if ($positional.Count -gt 2 -or $n.Extent.Text -match 'AdditionalChildPath') { & $flag $n 'Join-Path with >2 paths (5.1 takes two)' }
            }
            if ($cmd -eq 'ForEach-Object' -and $n.Extent.Text -match '-Parallel') { & $flag $n 'PS7-only -Parallel' }
            if ($cmd -eq 'Get-Content' -and $n.Extent.Text -match 'AsByteStream') { & $flag $n 'PS6+ -AsByteStream' }
            if ($cmd -eq 'Start-Process' -and $n.Extent.Text -match 'ArgumentList') { & $flag $n 'Start-Process -ArgumentList does not quote args on 5.1' }
            if ($cmd -in 'Invoke-WebRequest','Invoke-RestMethod') { & $flag $n 'IWR/IRM differ on 5.1 (IE parsing, proxy)' }
        }
    }
}
Write-Host "checked $($Files.Count) files, $bad problem(s)"
exit $bad
