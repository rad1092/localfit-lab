[CmdletBinding()]
param(
    [string]$OutputRoot
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"

if (-not $OutputRoot) {
    $OutputRoot = Join-Path $RepoRoot "deliverables\lightsail\$Timestamp"
}
$OutputRoot = [System.IO.Path]::GetFullPath($OutputRoot)
if (Test-Path -LiteralPath $OutputRoot) {
    throw "Output directory already exists: $OutputRoot"
}
New-Item -ItemType Directory -Path $OutputRoot | Out-Null

$StageRoot = Join-Path ([System.IO.Path]::GetTempPath()) "localfit-deploy-$Timestamp-$([guid]::NewGuid().ToString('N'))"
$AppRoot = Join-Path $StageRoot "app\localfit"
$DataRoot = Join-Path $StageRoot "data\localfit"
New-Item -ItemType Directory -Path $AppRoot, $DataRoot | Out-Null

function Copy-RequiredFile {
    param(
        [Parameter(Mandatory)][string]$Source,
        [Parameter(Mandatory)][string]$Destination
    )
    if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) {
        throw "Required file is missing: $Source"
    }
    $parent = Split-Path -Parent $Destination
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    Copy-Item -LiteralPath $Source -Destination $Destination
}

function Copy-RequiredTree {
    param(
        [Parameter(Mandatory)][string]$Source,
        [Parameter(Mandatory)][string]$Destination
    )
    if (-not (Test-Path -LiteralPath $Source -PathType Container)) {
        throw "Required directory is missing: $Source"
    }
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    & robocopy.exe $Source $Destination /E /XJ /R:2 /W:1 /NFL /NDL /NJH /NJS /NP `
        /XD "__pycache__" ".pytest_cache" ".mypy_cache" ".ruff_cache" ".next" "node_modules" ".venv" `
        /XF "*.pyc" "*.pyo" "*.tmp" "*.wal" "*.shm"
    if ($LASTEXITCODE -ge 8) {
        throw "robocopy failed with exit code $LASTEXITCODE for $Source"
    }
}

try {
    # Application source: explicit directories and build files only.
    Copy-RequiredTree `
        (Join-Path $RepoRoot "final_proj\backend\app") `
        (Join-Path $AppRoot "final_proj\backend\app")
    Copy-RequiredTree `
        (Join-Path $RepoRoot "final_proj\backend\scripts") `
        (Join-Path $AppRoot "final_proj\backend\scripts")
    Copy-RequiredFile `
        (Join-Path $RepoRoot "final_proj\backend\main.py") `
        (Join-Path $AppRoot "final_proj\backend\main.py")
    Copy-RequiredFile `
        (Join-Path $RepoRoot "final_proj\backend\requirements.txt") `
        (Join-Path $AppRoot "final_proj\backend\requirements.txt")

    Copy-RequiredTree `
        (Join-Path $RepoRoot "final_proj\frontend\src") `
        (Join-Path $AppRoot "final_proj\frontend\src")
    Copy-RequiredTree `
        (Join-Path $RepoRoot "final_proj\frontend\public") `
        (Join-Path $AppRoot "final_proj\frontend\public")
    $frontendFiles = @(
        "components.json",
        "eslint.config.mjs",
        "next-env.d.ts",
        "next.config.ts",
        "package-lock.json",
        "package.json",
        "pnpm-workspace.yaml",
        "postcss.config.mjs",
        "tsconfig.json"
    )
    foreach ($name in $frontendFiles) {
        Copy-RequiredFile `
            (Join-Path $RepoRoot "final_proj\frontend\$name") `
            (Join-Path $AppRoot "final_proj\frontend\$name")
    }

    Copy-RequiredTree `
        (Join-Path $RepoRoot "final_proj\resources") `
        (Join-Path $AppRoot "final_proj\resources")
    Copy-RequiredTree `
        (Join-Path $RepoRoot "scripts") `
        (Join-Path $AppRoot "scripts")
    Copy-RequiredTree `
        (Join-Path $RepoRoot "config") `
        (Join-Path $AppRoot "config")
    Copy-RequiredTree `
        (Join-Path $RepoRoot "deploy\lightsail") `
        (Join-Path $AppRoot "deploy\lightsail")
    Copy-RequiredFile `
        (Join-Path $RepoRoot "requirements-data.txt") `
        (Join-Path $AppRoot "requirements-data.txt")

    # Start with a clean production database. Reference, score, and spatial
    # tables remain; local accounts, reports, comments, caches, and logs do not.
    $Python = Join-Path $RepoRoot "final_proj\.venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $Python)) {
        $Python = (Get-Command python.exe -ErrorAction Stop).Source
    }
    $ProductionDatabase = Join-Path $DataRoot "final_proj\runtime\db\commercial.db"
    New-Item -ItemType Directory -Path (Split-Path -Parent $ProductionDatabase) | Out-Null
    & $Python `
        (Join-Path $RepoRoot "deploy\lightsail\create_production_database.py") `
        (Join-Path $RepoRoot "final_proj\runtime\db\commercial.db") `
        $ProductionDatabase
    if ($LASTEXITCODE -ne 0) {
        throw "Production database creation failed."
    }
    foreach ($directory in @("backups", "reports", "exports", "logs", "tmp", "auth", "admin")) {
        New-Item -ItemType Directory -Force -Path (Join-Path $DataRoot "final_proj\runtime\$directory") | Out-Null
    }

    $silverFiles = @(
        "silver_trade_area_master.csv",
        "silver_trade_area_boundary_geometry.csv",
        "silver_trade_area_boundary_vertices.csv",
        "silver_trade_area_boundary_spatial_index.csv",
        "silver_industry_master_seoul_open_data.csv",
        "silver_industry_bridge_seoul_sbdc.csv",
        "silver_sales_trade_area_q_industry.csv",
        "silver_store_trade_area_q_industry.csv",
        "silver_population_demand_q_area.csv",
        "silver_facility_trade_area_q.csv",
        "silver_change_index_trade_area_q.csv",
        "silver_consumption_trade_area_q.csv",
        "silver_living_migration_district_quarter_features.csv",
        "silver_rtms_commercial_trade_sgg_quarter.csv",
        "silver_sbdc_store_competition_trade_area_seoul_service_202603.csv",
        "silver_sbdc_store_poi_seoul_202603.csv",
        "silver_bus_stop_location_master.csv",
        "silver_subway_station_master.csv",
        "silver_bus_route_node_master.csv",
        "silver_news_evidence.csv",
        "silver_reb_rone_seoul_cost_proxy_latest.csv",
        "silver_mdis_seoul_tenant_lease_2023.csv",
        "silver_mdis_seoul_landlord_lease_2023.csv",
        "silver_localdata_business_license.csv"
    )
    foreach ($name in $silverFiles) {
        Copy-RequiredFile `
            (Join-Path $RepoRoot "datacorpus\_silver\$name") `
            (Join-Path $DataRoot "datacorpus\_silver\$name")
    }

    $goldFiles = @(
        "gold_trade_area_profile.csv",
        "gold_industry_taxonomy.csv",
        "gold_sales_strength_q_industry.csv",
        "gold_competition_q_industry.csv",
        "gold_demand_q_area.csv",
        "gold_growth_stability_q_industry.csv",
        "gold_growth_label_candidates_q_industry.csv",
        "gold_growth_rebound_candidate_q_industry.csv",
        "gold_accessibility_q_area.csv",
        "gold_cost_risk_q_area.csv",
        "gold_data_reliability_snapshot.csv",
        "gold_location_boundary_vertices.csv",
        "gold_location_input_lookup.csv",
        "gold_location_spatial_index.csv",
        "gold_seoul_lease_benchmark.csv",
        "gold_industry_selection_hierarchy.csv",
        "gold_industry_selection_tree.json"
    )
    foreach ($name in $goldFiles) {
        Copy-RequiredFile `
            (Join-Path $RepoRoot "datacorpus\_gold\$name") `
            (Join-Path $DataRoot "datacorpus\_gold\$name")
    }

    foreach ($directory in @(
        "datacorpus\_rule_validation",
        "datacorpus\_gold_validation",
        "datacorpus\_score_backtest",
        "datacorpus\_score_predictive_validation",
        "research\rule_validation"
    )) {
        Copy-RequiredTree `
            (Join-Path $RepoRoot $directory) `
            (Join-Path $DataRoot $directory)
    }

    $rawMetadataFiles = @(
        "duplicate_candidates.csv",
        "failed_downloads.csv",
        "ingest_manifest.csv",
        "living_migration_coverage_audit.csv",
        "living_migration_duplicate_groups.csv",
        "raw_file_duplicate_audit.csv",
        "raw_file_inventory.csv",
        "seoul_core_coverage_audit.csv",
        "source_registry.csv",
        "source_state_catalog.json",
        "store_competition_canonical_manifest.csv",
        "store_competition_duplicate_groups.csv",
        "store_competition_source_audit.csv",
        "store_trade_area_api_manifest_audit.csv"
    )
    foreach ($name in $rawMetadataFiles) {
        Copy-RequiredFile `
            (Join-Path $RepoRoot "datacorpus\_raw_ingest\$name") `
            (Join-Path $DataRoot "datacorpus\_raw_ingest\$name")
    }

    Copy-RequiredFile `
        (Join-Path $RepoRoot "datacorpus\_score_backtest_gold\gold_engine_backtest_component_metrics.csv") `
        (Join-Path $DataRoot "datacorpus\_score_backtest_gold\gold_engine_backtest_component_metrics.csv")
    $boundaryCandidates = @(
        Get-ChildItem -LiteralPath (Join-Path $RepoRoot "datacorpus\_unzipped") -Directory |
            Where-Object {
                $files = @(Get-ChildItem -LiteralPath $_.FullName -File)
                $extensions = @($files.Extension | Sort-Object -Unique)
                $totalBytes = ($files | Measure-Object Length -Sum).Sum
                $files.Count -eq 5 -and
                    ".shp" -in $extensions -and
                    ".dbf" -in $extensions -and
                    $totalBytes -gt 1MB -and
                    $totalBytes -lt 10MB
            }
    )
    if ($boundaryCandidates.Count -ne 1) {
        throw "Expected exactly one official trade-area boundary directory, found $($boundaryCandidates.Count)."
    }
    Copy-RequiredTree `
        $boundaryCandidates[0].FullName `
        (Join-Path $DataRoot "datacorpus\_unzipped\$($boundaryCandidates[0].Name)")

    Copy-RequiredTree `
        (Join-Path $RepoRoot "datacorpus\_final\spatial_od") `
        (Join-Path $DataRoot "datacorpus\_final\spatial_od")

    $rootDataFiles = @(Get-ChildItem -LiteralPath (Join-Path $RepoRoot "datacorpus") -File -Filter "*.csv")
    if (-not $rootDataFiles) {
        throw "No root-level data CSV files were found."
    }
    foreach ($file in $rootDataFiles) {
        Copy-RequiredFile `
            $file.FullName `
            (Join-Path $DataRoot "datacorpus\$($file.Name)")
    }

    $latestScore = Get-ChildItem `
        -LiteralPath (Join-Path $RepoRoot "datacorpus\_location_judgement_outputs") `
        -Filter "loc_score_v2_batch_*.csv" |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if (-not $latestScore) {
        throw "Latest location-score batch was not found."
    }
    Copy-RequiredFile `
        $latestScore.FullName `
        (Join-Path $DataRoot "datacorpus\_location_judgement_outputs\$($latestScore.Name)")
    $latestManifest = [System.IO.Path]::ChangeExtension($latestScore.FullName, ".manifest.json")
    Copy-RequiredFile `
        $latestManifest `
        (Join-Path $DataRoot "datacorpus\_location_judgement_outputs\$([System.IO.Path]::GetFileName($latestManifest))")

    $AppArchive = Join-Path $OutputRoot "localfit-app-$Timestamp.tar.gz"
    $DataArchive = Join-Path $OutputRoot "localfit-data-$Timestamp.tar.gz"
    & tar.exe -czf $AppArchive -C (Join-Path $StageRoot "app") "localfit"
    if ($LASTEXITCODE -ne 0) {
        throw "Application archive creation failed."
    }
    & tar.exe -czf $DataArchive -C (Join-Path $StageRoot "data") "localfit"
    if ($LASTEXITCODE -ne 0) {
        throw "Data archive creation failed."
    }

    $hashLines = foreach ($archive in @($AppArchive, $DataArchive)) {
        $hash = Get-FileHash -Algorithm SHA256 -LiteralPath $archive
        "$($hash.Hash.ToLowerInvariant())  $([System.IO.Path]::GetFileName($archive))"
    }
    $hashLines | Set-Content -LiteralPath (Join-Path $OutputRoot "SHA256SUMS.txt") -Encoding ascii

    Get-ChildItem -LiteralPath $OutputRoot -File |
        Select-Object Name, Length, LastWriteTime |
        Format-Table -AutoSize
    Write-Output "OUTPUT_ROOT=$OutputRoot"
}
finally {
    if (Test-Path -LiteralPath $StageRoot) {
        $resolvedStage = [System.IO.Path]::GetFullPath($StageRoot)
        $resolvedTemp = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
        if (-not $resolvedStage.StartsWith($resolvedTemp, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to remove staging directory outside the temp root: $resolvedStage"
        }
        Remove-Item -LiteralPath $resolvedStage -Recurse -Force
    }
}
