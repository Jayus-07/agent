# Test Coverage Report

## 📊 Overview

This document provides a summary of our test coverage metrics and monitoring setup.

### Current Status

| Module | Coverage | Target | Status |
|--------|----------|--------|--------|
| **Backend Tools** | ~3% | ≥55% | ⚠️ In Progress |
| **SQL Tool** | ~60% | N/A | ✅ Good |
| **Data Collection** | ~55% | N/A | ✅ Good |
| **Web Search/Crawl** | 0% | ≥70% | ⏸️ Pending |
| **Report/Export** | 0% | ≥70% | ⏸️ Pending |
| **Email** | 0% | ≥70% | ⏸️ Pending |

**Overall Backend Coverage**: ~3% (Baseline)  
**Target**: 55%+ overall, 80%+ for P0/P1 tools

---

## 🔧 How to Generate Coverage Reports

### Local Development

```bash
# Run all tests with full coverage report
pytest --cov=backend --cov-report=html --cov-report=term-missing

# View HTML coverage report locally
open htmlcov/index.html
```

### For Specific Modules

```bash
# Only test SQL tools with coverage
pytest backend/tests/tools/test_sql_tool.py --cov=backend.tools.sql

# Get line-by-line coverage info
pytest --cov=backend.tests --cov-report=term-missing
```

### CI/CD Coverage Upload

Coverage reports are automatically uploaded to [Codecov](https://codecov.io/) on every PR:

1. `unit-tests.yml` → Full backend coverage
2. `tool_quality.yml` → Tool-specific coverage
3. Reruns daily at 09:00 UTC on main branch

---

## 📈 Coverage Monitoring Setup

### Codecov Integration

We use **Codecov.io** (free for open source projects) for automated coverage tracking:

- **Repository URL**: https://github.com/[your-repo]/settings/secrets/actions
- **Secret Key**: `CODECOV_TOKEN` (from your Codecov account)

#### Setup Steps:

1. Sign up at https://codecov.io/
2. Connect your GitHub repository
3. Copy the upload token
4. Add to GitHub Actions secrets: `CODECOV_TOKEN`
5. Configuration file: `.codecov.yml` (already in repo)

### Local Coverage Analysis

```bash
# Generate detailed coverage for specific files
pytest --cov=backend.tools.sql \
       --cov-report=html:sql-cov \
       --cov-report=xml:coverage-sql.xml

# Analyze missing lines
coverage report -m --show-missing backend/tools/sql.py
```

---

## 🎯 Coverage Thresholds

| Environment | Minimum Coverage | Action |
|-------------|------------------|---------|
| **Local** | 55% | Fails build |
| **CI Main Branch** | Auto | Monitors trend |
| **PR Patch Coverage** | 80% | Recommended |
| **New Code** | 80% | Required |

---

## 📋 Coverage by Component

### High Priority Components (Current Week)

✅ **Completed:**
- `backend/tools/sql.py` (~60%): 19 tests, core functionality
- `backend/tools/data_collection.py` (~55%): 33 tests, basic + edge cases

🔄 **In Progress:**
- `backend/tools/web_search.py`: Web scraping integration
- `backend/tools/export.py`: CSV export utilities

⏸️ **Pending:**
- `backend/report/*.py`: Report generation logic
- `backend/email/*.py`: Email sending utilities
- `backend/competitor/*.py`: Competitor analysis

---

## 🛠️ Maintenance & Updates

### Weekly Tasks

1. Review Codecov dashboard for new failures
2. Check uncovered lines in recent PRs
3. Update baseline metrics if needed

### Monthly Goals

1. Reach 60% overall coverage
2. Achieve 80%+ for P0 tools
3. Integrate coverage into Sprint reviews

### Best Practices

- Each new feature must include unit tests
- Coverage should increase with each commit
- PRs must maintain or improve coverage
- Document gaps explicitly if no simple fix exists

---

## 📝 Related Documentation

- [TOOL_SKILL_OPTIMIZATION_PLAN.md](./docs/TOOL_SKILL_OPTIMIZATION_PLAN.md)
- [CONTRIBUTING.md](./CONTRIBUTING.md)
- [pytest.ini](./pytest.ini)
