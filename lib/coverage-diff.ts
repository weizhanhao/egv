#!/usr/bin/env tsx
/**
 * coverage-diff.ts — the heart of the Regression Auditor.
 *
 * Reads:
 *   - A unified git diff (file paths + changed line numbers)
 *   - A Vitest coverage-final.json (Istanbul-format, per-file statement coverage)
 *
 * Computes:
 *   - For each changed line: is it executed by at least one test? (covered/uncovered)
 *   - Aggregate: blast radius = (covered lines + tests they hit) / (uncovered lines = risk)
 *
 * Emits structured JSON to stdout for the orchestrator (SKILL.md) to consume.
 *
 * Usage:
 *   tsx coverage-diff.ts <diff-file-path> <coverage-json-path> <project-root>
 *
 * Notes:
 *   - We don't attribute coverage to specific tests in v0 (Istanbul doesn't natively expose this).
 *     "Covered" means "some test in the suite hit this line at least once". Test PASS/FAIL is
 *     checked separately by the orchestrator.
 *   - Diff paths are relative to repo root. Coverage paths are absolute. We normalize.
 */

import { readFileSync, statSync } from "node:fs";
import { resolve, isAbsolute } from "node:path";

type DiffHunk = { newStart: number; newCount: number };
type DiffFile = { path: string; hunks: DiffHunk[]; changedLines: number[] };

type IstanbulStmtLoc = { start: { line: number }; end: { line: number } };
type IstanbulFileCoverage = {
  path: string;
  statementMap: Record<string, IstanbulStmtLoc>;
  s: Record<string, number>;
};
type CoverageJson = Record<string, IstanbulFileCoverage>;

type FileBlastRadius = {
  file: string;
  is_test_file: boolean;
  changed_lines: number[];
  covered_lines: number[];
  uncovered_lines: number[];
  coverage_status: "fully_covered" | "partially_covered" | "uncovered" | "not_in_coverage" | "test_file_excluded";
};

const TEST_FILE_REGEX = /(?:\.test\.(?:ts|tsx|js|jsx|mjs|mts))$|(?:\.spec\.(?:ts|tsx|js|jsx))$|(?:^|\/)tests\/|(?:^|\/)__tests__\//;

function isTestFile(path: string): boolean {
  return TEST_FILE_REGEX.test(path);
}

type WeightPattern = { glob: string; weight: number; reason: string };
type WeightConfig = { patterns: WeightPattern[]; default: number };

function loadWeights(path: string | undefined): WeightConfig {
  if (!path) return { patterns: [], default: 1.0 };
  const data = JSON.parse(readFileSync(path, "utf-8"));
  return {
    patterns: data.patterns ?? [],
    default: typeof data.default === "number" ? data.default : 1.0,
  };
}

function globToRegex(glob: string): RegExp {
  let re = glob
    .replace(/\./g, "\\.")
    .replace(/\{([^}]+)\}/g, (_, inner) => `(${inner.split(",").join("|")})`)
    .replace(/\*\*/g, "::DOUBLESTAR::")
    .replace(/\*/g, "[^/]*")
    .replace(/::DOUBLESTAR::/g, ".*");
  return new RegExp(`^${re}$`);
}

function weightForFile(path: string, config: WeightConfig): number {
  for (const p of config.patterns) {
    if (globToRegex(p.glob).test(path)) return p.weight;
  }
  return config.default;
}

type BlastRadiusReport = {
  generated_at: string;
  diff_file: string;
  coverage_file: string;
  project_root: string;
  files: FileBlastRadius[];
  summary: {
    total_files_in_diff: number;
    production_files_in_diff: number;
    test_files_in_diff: number;
    files_fully_covered: number;
    files_partially_covered: number;
    files_uncovered: number;
    files_not_in_coverage: number;
    total_changed_lines: number;
    production_changed_lines: number;
    test_changed_lines: number;
    covered_changed_lines: number;
    uncovered_changed_lines: number;
    coverage_ratio: number;
    weighted_production_changed_lines: number;
    weighted_covered_changed_lines: number;
    weighted_coverage_ratio: number;
  };
  confidence_inputs: {
    coverage_data_age_seconds: number | null;
    project_has_coverage_for_diff_paths: boolean;
  };
};

function parseDiff(diffText: string): DiffFile[] {
  const files: DiffFile[] = [];
  let current: DiffFile | null = null;

  const lines = diffText.split("\n");
  for (const line of lines) {
    const fileMatch = line.match(/^\+\+\+ b\/(.+)$/);
    if (fileMatch) {
      if (current) files.push(current);
      current = { path: fileMatch[1], hunks: [], changedLines: [] };
      continue;
    }
    const hunkMatch = line.match(/^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@/);
    if (hunkMatch && current) {
      const newStart = Number(hunkMatch[1]);
      const newCount = hunkMatch[2] !== undefined ? Number(hunkMatch[2]) : 1;
      current.hunks.push({ newStart, newCount });
      for (let i = 0; i < newCount; i++) {
        current.changedLines.push(newStart + i);
      }
      continue;
    }
  }
  if (current) files.push(current);
  return files;
}

function loadCoverage(coveragePath: string): CoverageJson {
  const raw = readFileSync(coveragePath, "utf-8");
  return JSON.parse(raw) as CoverageJson;
}

function isLineCovered(
  fileCoverage: IstanbulFileCoverage,
  lineNumber: number,
): boolean {
  for (const [stmtId, loc] of Object.entries(fileCoverage.statementMap)) {
    if (lineNumber >= loc.start.line && lineNumber <= loc.end.line) {
      const hits = fileCoverage.s[stmtId] ?? 0;
      if (hits > 0) return true;
    }
  }
  return false;
}

function fileInCoverageData(
  diffPath: string,
  coverage: CoverageJson,
  projectRoot: string,
): IstanbulFileCoverage | null {
  const absDiffPath = resolve(projectRoot, diffPath);
  if (coverage[absDiffPath]) return coverage[absDiffPath];

  for (const [coveragePath, fileCov] of Object.entries(coverage)) {
    if (coveragePath.endsWith(diffPath)) return fileCov;
  }
  return null;
}

function computeBlastRadius(
  diffFiles: DiffFile[],
  coverage: CoverageJson,
  projectRoot: string,
): FileBlastRadius[] {
  return diffFiles.map((file) => {
    if (isTestFile(file.path)) {
      return {
        file: file.path,
        is_test_file: true,
        changed_lines: file.changedLines,
        covered_lines: [],
        uncovered_lines: [],
        coverage_status: "test_file_excluded" as const,
      };
    }
    const fileCov = fileInCoverageData(file.path, coverage, projectRoot);
    if (!fileCov) {
      return {
        file: file.path,
        is_test_file: false,
        changed_lines: file.changedLines,
        covered_lines: [],
        uncovered_lines: file.changedLines,
        coverage_status: "not_in_coverage" as const,
      };
    }
    const covered: number[] = [];
    const uncovered: number[] = [];
    for (const line of file.changedLines) {
      if (isLineCovered(fileCov, line)) covered.push(line);
      else uncovered.push(line);
    }
    let status: FileBlastRadius["coverage_status"];
    if (covered.length === file.changedLines.length) status = "fully_covered";
    else if (covered.length === 0) status = "uncovered";
    else status = "partially_covered";

    return {
      file: file.path,
      is_test_file: false,
      changed_lines: file.changedLines,
      covered_lines: covered,
      uncovered_lines: uncovered,
      coverage_status: status,
    };
  });
}

function summarize(files: FileBlastRadius[], weights: WeightConfig): BlastRadiusReport["summary"] {
  const productionFiles = files.filter((f) => !f.is_test_file);
  const testFiles = files.filter((f) => f.is_test_file);
  const totalChangedLines = files.reduce((sum, f) => sum + f.changed_lines.length, 0);
  const productionChangedLines = productionFiles.reduce((sum, f) => sum + f.changed_lines.length, 0);
  const testChangedLines = testFiles.reduce((sum, f) => sum + f.changed_lines.length, 0);
  const coveredChangedLines = productionFiles.reduce((sum, f) => sum + f.covered_lines.length, 0);
  const uncoveredChangedLines = productionFiles.reduce((sum, f) => sum + f.uncovered_lines.length, 0);

  let weightedProd = 0;
  let weightedCovered = 0;
  for (const f of productionFiles) {
    const w = weightForFile(f.file, weights);
    weightedProd += f.changed_lines.length * w;
    weightedCovered += f.covered_lines.length * w;
  }

  return {
    total_files_in_diff: files.length,
    production_files_in_diff: productionFiles.length,
    test_files_in_diff: testFiles.length,
    files_fully_covered: files.filter((f) => f.coverage_status === "fully_covered").length,
    files_partially_covered: files.filter((f) => f.coverage_status === "partially_covered").length,
    files_uncovered: files.filter((f) => f.coverage_status === "uncovered").length,
    files_not_in_coverage: files.filter((f) => f.coverage_status === "not_in_coverage").length,
    total_changed_lines: totalChangedLines,
    production_changed_lines: productionChangedLines,
    test_changed_lines: testChangedLines,
    covered_changed_lines: coveredChangedLines,
    uncovered_changed_lines: uncoveredChangedLines,
    coverage_ratio: productionChangedLines === 0 ? 1 : coveredChangedLines / productionChangedLines,
    weighted_production_changed_lines: weightedProd,
    weighted_covered_changed_lines: weightedCovered,
    weighted_coverage_ratio: weightedProd === 0 ? 1 : weightedCovered / weightedProd,
  };
}

function main() {
  const argv = process.argv.slice(2);
  const [diffPath, coveragePath, projectRoot, ...rest] = argv;
  if (!diffPath || !coveragePath || !projectRoot) {
    console.error("Usage: tsx coverage-diff.ts <diff-file> <coverage-json> <project-root> [--weights <weights.json>]");
    process.exit(2);
  }
  const weightsIdx = rest.indexOf("--weights");
  const weightsPath = weightsIdx >= 0 ? rest[weightsIdx + 1] : undefined;
  const weights = loadWeights(weightsPath);
  const absProjectRoot = isAbsolute(projectRoot) ? projectRoot : resolve(projectRoot);

  const diffText = readFileSync(diffPath, "utf-8");
  const coverage = loadCoverage(coveragePath);
  const diffFiles = parseDiff(diffText);
  const files = computeBlastRadius(diffFiles, coverage, absProjectRoot);
  const summary = summarize(files, weights);

  let coverageDataAgeSeconds: number | null = null;
  try {
    const stat = statSync(coveragePath);
    coverageDataAgeSeconds = Math.round((Date.now() - stat.mtimeMs) / 1000);
  } catch {
    coverageDataAgeSeconds = null;
  }

  const report: BlastRadiusReport = {
    generated_at: new Date().toISOString(),
    diff_file: diffPath,
    coverage_file: coveragePath,
    project_root: absProjectRoot,
    files,
    summary,
    confidence_inputs: {
      coverage_data_age_seconds: coverageDataAgeSeconds,
      project_has_coverage_for_diff_paths: files.some((f) => f.coverage_status !== "not_in_coverage"),
    },
  };

  process.stdout.write(JSON.stringify(report, null, 2));
}

main();
