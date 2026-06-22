#!/bin/bash
git fetch --prune
for branch in $(git branch --merged main | grep -v 'main\|master'); do
  echo "Deleting merged branch: $branch"
  git branch -d "$branch"
done
