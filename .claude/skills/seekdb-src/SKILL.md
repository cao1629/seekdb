---
name: seekdb-src
description: >
  SeekDB source code exploration assistant. After explaining code (methods, variables,
  classes, etc.), offers to save the explanation to Obsidian's SeekDB knowledge base.
triggers:
  - "what does * do"
  - "what is * for"
  - "explain *"
  - "how does * work"
---

# SeekDB Source Code Assistant

When the user asks about SeekDB source code:
- What does a method/function do?
- What is a variable/constant for?
- How does a class/struct work?
- Explain a code pattern or algorithm

## Workflow

1. **Answer the question** about the code
2. **After answering**, ask:
   > "Save this to Obsidian's SeekDB folder?"
3. If user confirms:
   - Search for the SeekDB folder: `find ~/obsidian -type d -iname "*seekdb*"`
   - Expected location: `~/obsidian/DB/Catalog/SeekDB/`
   - Create a markdown file with the explanation
   - Filename should be descriptive (e.g., "VecValueTypeClass enum.md", "BatchAggregateWrapper template.md")

## File Format

```markdown
# [Topic Name]

## Overview
[Brief description]

## Details
[Detailed explanation]

## Code Location
- File: `path/to/file.h`
- Line: XXX

## Related
- [Links to related concepts]

## Tags
#seekdb #[relevant-tags]
```

## SeekDB Folder Location

Primary: `~/obsidian/DB/Catalog/SeekDB/`

If not found, search with:
```bash
find ~/obsidian -type d -iname "*seekdb*" 2>/dev/null
```
