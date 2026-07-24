param(
    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory
)

$ErrorActionPreference = "Stop"
$workspaceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$outputPath = [IO.Path]::GetFullPath($OutputDirectory)

if (Test-Path -LiteralPath $outputPath) {
    throw "Output directory already exists: $outputPath"
}

function Copy-Tree {
    param(
        [string]$Source,
        [string]$Destination,
        [string[]]$ExcludeDirectories = @(),
        [string[]]$ExcludeFiles = @()
    )
    $arguments = @($Source, $Destination, "/E", "/R:1", "/W:1", "/NFL", "/NDL", "/NJH", "/NJS", "/NP")
    if ($ExcludeDirectories.Count) {
        $arguments += "/XD"
        $arguments += $ExcludeDirectories
    }
    if ($ExcludeFiles.Count) {
        $arguments += "/XF"
        $arguments += $ExcludeFiles
    }
    & robocopy @arguments | Out-Null
    if ($LASTEXITCODE -ge 8) {
        throw "robocopy failed with exit code $LASTEXITCODE for $Source"
    }
}

New-Item -ItemType Directory -Path $outputPath | Out-Null

Copy-Tree `
    (Join-Path $workspaceRoot "final_proj") `
    (Join-Path $outputPath "final_proj") `
    @(
        (Join-Path $workspaceRoot "final_proj\.venv"),
        (Join-Path $workspaceRoot "final_proj\frontend\node_modules"),
        (Join-Path $workspaceRoot "final_proj\frontend\.next"),
        (Join-Path $workspaceRoot "final_proj\runtime")
    ) `
    @(".env", ".env.local", "*.pyc", "*.pyo", "*.log", "*.tsbuildinfo")

Copy-Tree `
    (Join-Path $workspaceRoot "scripts") `
    (Join-Path $outputPath "scripts") `
    @((Join-Path $workspaceRoot "scripts\__pycache__")) `
    @("*.pyc", "*.pyo", "*.log")
Copy-Tree (Join-Path $workspaceRoot "docs") (Join-Path $outputPath "docs") @() @("*.log")
Copy-Tree `
    (Join-Path $workspaceRoot "research") `
    (Join-Path $outputPath "research") `
    @((Join-Path $workspaceRoot "research\notebooks\.ipynb_checkpoints")) `
    @("*.pyc", "*.log")

foreach ($file in @("README.md", "HANDOFF.md", "requirements-data.txt", ".env.example", ".gitignore")) {
    Copy-Item -LiteralPath (Join-Path $workspaceRoot $file) -Destination (Join-Path $outputPath $file)
}

$dataDirectories = @(
    "datacorpus\_gold",
    "datacorpus\_silver",
    "datacorpus\_rule_validation",
    "datacorpus\_gold_validation",
    "datacorpus\_score_backtest",
    "datacorpus\_location_judgement_outputs",
    "datacorpus\_raw_ingest",
    "datacorpus\_final\spatial_od",
    "datacorpus\_unzipped",
    "final_proj\runtime\db"
)
foreach ($directory in $dataDirectories) {
    New-Item -ItemType Directory -Force -Path (Join-Path $outputPath $directory) | Out-Null
}

Copy-Tree `
    (Join-Path $workspaceRoot "datacorpus\_rule_validation") `
    (Join-Path $outputPath "datacorpus\_rule_validation")
Copy-Tree `
    (Join-Path $workspaceRoot "datacorpus\_gold_validation") `
    (Join-Path $outputPath "datacorpus\_gold_validation")
$shapeSource = Get-ChildItem (Join-Path $workspaceRoot "datacorpus\_unzipped") -Directory |
    Where-Object { $_.Name -like "*(*)*" -and (Get-ChildItem $_.FullName -Filter "*.shp" -File) } |
    Select-Object -First 1
if (-not $shapeSource) {
    throw "Trade-area shapefile directory was not found."
}
Copy-Tree `
    $shapeSource.FullName `
    (Join-Path $outputPath ("datacorpus\_unzipped\" + $shapeSource.Name))

$goldFiles = @(
    "gold_trade_area_profile.csv",
    "gold_sales_strength_q_industry.csv",
    "gold_competition_q_industry.csv",
    "gold_demand_q_area.csv",
    "gold_growth_stability_q_industry.csv",
    "gold_growth_rebound_candidate_q_industry.csv",
    "gold_accessibility_q_area.csv",
    "gold_cost_risk_q_area.csv",
    "gold_location_input_lookup.csv",
    "gold_location_spatial_index.csv",
    "gold_location_boundary_vertices.csv",
    "gold_industry_selection_hierarchy.csv",
    "gold_industry_selection_tree.json",
    "gold_industry_taxonomy.csv"
)
foreach ($file in $goldFiles) {
    Copy-Item `
        -LiteralPath (Join-Path $workspaceRoot "datacorpus\_gold\$file") `
        -Destination (Join-Path $outputPath "datacorpus\_gold\$file")
}

$silverFiles = @(
    "silver_living_migration_district_quarter_features.csv",
    "silver_rtms_commercial_trade_sgg_quarter.csv",
    "silver_sbdc_store_competition_trade_area_seoul_service_202603.csv",
    "silver_reb_rone_seoul_cost_proxy_latest.csv",
    "silver_sbdc_store_poi_seoul_202603.csv",
    "silver_bus_stop_location_master.csv",
    "silver_subway_station_master.csv"
)
foreach ($file in $silverFiles) {
    Copy-Item `
        -LiteralPath (Join-Path $workspaceRoot "datacorpus\_silver\$file") `
        -Destination (Join-Path $outputPath "datacorpus\_silver\$file")
}

Copy-Item `
    -LiteralPath (Join-Path $workspaceRoot "datacorpus\_score_backtest\location_score_backtest_recommended_weights.csv") `
    -Destination (Join-Path $outputPath "datacorpus\_score_backtest\location_score_backtest_recommended_weights.csv")
Copy-Item `
    -LiteralPath (Join-Path $workspaceRoot "datacorpus\_location_judgement_outputs\loc_score_v2_batch_20261_20260708_075336.csv") `
    -Destination (Join-Path $outputPath "datacorpus\_location_judgement_outputs\loc_score_v2_batch_20261_20260708_075336.csv")
$spatialReferenceFiles = Get-ChildItem (Join-Path $workspaceRoot "datacorpus\_final\spatial_od") -File |
    Where-Object { $_.Name -like "*SBDC*247.csv" -or $_.Name -like "*SBDC*.csv" } |
    Sort-Object Name -Unique
if ($spatialReferenceFiles.Count -lt 2) {
    throw "Required SBDC spatial reference files were not found."
}
$spatialReferenceFiles | Copy-Item -Destination (Join-Path $outputPath "datacorpus\_final\spatial_od")

$consumptionSource = Get-ChildItem (Join-Path $workspaceRoot "datacorpus") -File |
    Where-Object { $_.Name -like "*.csv" -and $_.Length -gt 7.25MB -and $_.Length -lt 7.4MB } |
    Select-Object -First 1
if (-not $consumptionSource) {
    throw "Consumption source CSV was not found."
}
Copy-Item -LiteralPath $consumptionSource.FullName -Destination (Join-Path $outputPath "datacorpus")
Get-ChildItem (Join-Path $workspaceRoot "datacorpus\_raw_ingest") -File |
    Copy-Item -Destination (Join-Path $outputPath "datacorpus\_raw_ingest")

New-Item -ItemType Directory -Force -Path (Join-Path $outputPath "final_proj\runtime") | Out-Null
Copy-Item `
    -LiteralPath (Join-Path $workspaceRoot "final_proj\runtime\README.md") `
    -Destination (Join-Path $outputPath "final_proj\runtime\README.md")

$python = Join-Path $workspaceRoot "final_proj\.venv\Scripts\python.exe"
& $python `
    (Join-Path $workspaceRoot "scripts\create_handoff_database.py") `
    (Join-Path $workspaceRoot "final_proj\runtime\db\commercial.db") `
    (Join-Path $outputPath "final_proj\runtime\db\commercial.db")
if ($LASTEXITCODE -ne 0) {
    throw "Failed to create the handoff database."
}

$files = Get-ChildItem -LiteralPath $outputPath -Recurse -File
$size = ($files | Measure-Object Length -Sum).Sum
[pscustomobject]@{
    OutputDirectory = $outputPath
    FileCount = $files.Count
    SizeGB = [math]::Round($size / 1GB, 2)
}
