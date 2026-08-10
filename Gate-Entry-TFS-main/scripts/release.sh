#!/bin/bash

# Release script for Gate Entry module
# Usage: ./scripts/release.sh <version> [--dry-run]
# Example: ./scripts/release.sh 1.0.0

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if version is provided
if [ -z "$1" ]; then
    echo -e "${RED}Error: Version number is required${NC}"
    echo "Usage: $0 <version> [--dry-run]"
    echo "Example: $0 1.0.0"
    exit 1
fi

VERSION=$1
DRY_RUN=false

# Check for dry-run flag
if [ "$2" == "--dry-run" ]; then
    DRY_RUN=true
    echo -e "${YELLOW}Running in dry-run mode (no changes will be made)${NC}"
fi

# Validate version format (semantic versioning)
if ! [[ $VERSION =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9.-]+)?$ ]]; then
    echo -e "${RED}Error: Invalid version format. Use semantic versioning (e.g., 1.0.0, 1.0.0-rc.1)${NC}"
    exit 1
fi

# Get the directory where the script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

# Check if we're in a git repository
if ! git rev-parse --git-dir > /dev/null 2>&1; then
    echo -e "${RED}Error: Not a git repository${NC}"
    exit 1
fi

# Check if there are uncommitted changes
if [ -n "$(git status --porcelain)" ]; then
    echo -e "${RED}Error: You have uncommitted changes. Please commit or stash them first.${NC}"
    git status --short
    exit 1
fi

# Get current branch
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)

# Check if we're on develop branch
if [ "$CURRENT_BRANCH" != "develop" ]; then
    echo -e "${YELLOW}Warning: You're not on the 'develop' branch (current: $CURRENT_BRANCH)${NC}"
    read -p "Continue anyway? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Check if tag already exists
if git rev-parse "v$VERSION" >/dev/null 2>&1; then
    echo -e "${RED}Error: Tag v$VERSION already exists${NC}"
    exit 1
fi

# Get current version
CURRENT_VERSION=$(grep -E "^__version__\s*=" gate_entry/__init__.py | sed -E "s/^__version__\s*=\s*['\"](.*)['\"]/\1/")

echo -e "${GREEN}Release Information:${NC}"
echo "  Current version: $CURRENT_VERSION"
echo "  New version:     $VERSION"
echo "  Tag:             v$VERSION"
echo "  Branch:          $CURRENT_BRANCH"
echo ""

# Confirm release
if [ "$DRY_RUN" = false ]; then
    read -p "Proceed with release? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Release cancelled."
        exit 0
    fi
fi

# Update version in __init__.py
echo -e "${GREEN}Updating version in gate_entry/__init__.py...${NC}"
if [ "$DRY_RUN" = false ]; then
    sed -i.bak "s/^__version__ = .*/__version__ = \"$VERSION\"/" gate_entry/__init__.py
    rm -f gate_entry/__init__.py.bak
    echo "  ✓ Updated __version__ to \"$VERSION\""
else
    echo "  [DRY-RUN] Would update __version__ to \"$VERSION\""
fi

# Check if CHANGELOG.md exists and prompt for update
if [ -f "CHANGELOG.md" ]; then
    echo -e "${YELLOW}Note: Please update CHANGELOG.md with release notes for version $VERSION${NC}"
    echo "  Add a new section for this version before committing."
else
    echo -e "${YELLOW}Note: CHANGELOG.md not found. Consider creating one to track changes.${NC}"
fi

# Create release branch
RELEASE_BRANCH="release/v$VERSION"
echo -e "${GREEN}Creating release branch: $RELEASE_BRANCH...${NC}"

if [ "$DRY_RUN" = false ]; then
    git checkout -b "$RELEASE_BRANCH"
    echo "  ✓ Created branch $RELEASE_BRANCH"
else
    echo "  [DRY-RUN] Would create branch $RELEASE_BRANCH"
fi

# Commit version change
echo -e "${GREEN}Committing version change...${NC}"
if [ "$DRY_RUN" = false ]; then
    git add gate_entry/__init__.py
    git commit -m "chore: bump version to $VERSION"
    echo "  ✓ Committed version change"
else
    echo "  [DRY-RUN] Would commit version change"
fi

# Create annotated tag
echo -e "${GREEN}Creating git tag v$VERSION...${NC}"
if [ "$DRY_RUN" = false ]; then
    # Prompt for release notes
    echo "Enter release notes for this version (press Ctrl+D when done, or leave empty for default):"
    RELEASE_NOTES=$(cat)
    
    if [ -z "$RELEASE_NOTES" ]; then
        RELEASE_NOTES="Release version $VERSION

Compatible with ERPNext v15.x"
    fi
    
    git tag -a "v$VERSION" -m "$RELEASE_NOTES"
    echo "  ✓ Created tag v$VERSION"
else
    echo "  [DRY-RUN] Would create tag v$VERSION"
fi

# Summary
echo ""
echo -e "${GREEN}✓ Release preparation complete!${NC}"
echo ""
echo "Next steps:"
echo "  1. Review the changes:"
echo "     git log --oneline -5"
echo ""
echo "  2. Test the release branch:"
echo "     bench --site [your-site] run-tests --app gate_entry"
echo ""
echo "  3. Push the release branch and tag:"
echo "     git push origin $RELEASE_BRANCH"
echo "     git push origin v$VERSION"
echo ""
echo "  4. After testing, merge to main (if applicable) and develop:"
echo "     git checkout main"
echo "     git merge --no-ff $RELEASE_BRANCH -m \"Release v$VERSION\""
echo "     git push origin main"
echo ""
echo "     git checkout develop"
echo "     git merge --no-ff $RELEASE_BRANCH -m \"Merge release v$VERSION into develop\""
echo "     git push origin develop"
echo ""
echo "  5. Create a GitHub release (if using GitHub):"
echo "     - Go to Releases → Draft a new release"
echo "     - Select tag v$VERSION"
echo "     - Add release notes from CHANGELOG.md"
echo ""

