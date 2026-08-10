# Quick Release Guide

This is a quick reference for creating a new release. For detailed information, see [RELEASE.md](../RELEASE.md).

## Quick Release Steps

### 1. Prepare Release (Automated)

```bash
# Make sure you're on develop branch
git checkout develop
git pull origin develop

# Run the release script
./scripts/release.sh 1.0.0
```

The script will:
- Validate version format
- Check for uncommitted changes
- Update version in `gate_entry/__init__.py`
- Create a release branch
- Commit the version change
- Create an annotated git tag

### 2. Update CHANGELOG.md

Before committing, update `CHANGELOG.md` with:
- New features
- Bug fixes
- Breaking changes (if any)
- Date of release

### 3. Test the Release

```bash
# Run tests
bench --site [your-site] run-tests --app gate_entry

# Manual testing on staging environment
```

### 4. Push Release Branch and Tag

```bash
# Push release branch
git push origin release/v1.0.0

# Push tag
git push origin v1.0.0
```

### 5. Merge to Main (if applicable)

```bash
git checkout main
git merge --no-ff release/v1.0.0 -m "Release v1.0.0"
git push origin main
```

### 6. Merge Back to Develop

```bash
git checkout develop
git merge --no-ff release/v1.0.0 -m "Merge release v1.0.0 into develop"
git push origin develop
```

### 7. Create GitHub Release

1. Go to GitHub → Releases → Draft a new release
2. Select tag `v1.0.0`
3. Title: `Gate Entry v1.0.0`
4. Copy changelog entries
5. Publish

## Version Bumping

- **Patch** (1.0.0 → 1.0.1): Bug fixes
- **Minor** (1.0.0 → 1.1.0): New features (backward compatible)
- **Major** (1.0.0 → 2.0.0): Breaking changes

## Hotfix Process

For urgent production fixes:

```bash
# Create hotfix from latest release tag
git checkout -b hotfix/v1.0.1 v1.0.0

# Make fixes, update version, test
# Then tag and merge to both main and develop
```

## Dry Run

Test the release script without making changes:

```bash
./scripts/release.sh 1.0.0 --dry-run
```

