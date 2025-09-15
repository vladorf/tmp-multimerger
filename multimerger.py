#!/usr/bin/env python3

import argparse
import os
import sys
import requests
from typing import Dict, List, Optional, Any


def colorize_diff(diff_text: str) -> str:
    """Add ANSI color codes to diff text"""
    lines = diff_text.split('\n')
    colored_lines = []
    
    for line in lines:
        if line.startswith('+++') or line.startswith('---'):
            # File headers in bold
            colored_lines.append(f'\033[1m{line}\033[0m')
        elif line.startswith('@@'):
            # Hunk headers in cyan
            colored_lines.append(f'\033[36m{line}\033[0m')
        elif line.startswith('+'):
            # Added lines in green
            colored_lines.append(f'\033[32m{line}\033[0m')
        elif line.startswith('-'):
            # Removed lines in red
            colored_lines.append(f'\033[31m{line}\033[0m')
        else:
            # Context lines unchanged
            colored_lines.append(line)
    
    return '\n'.join(colored_lines)


class GitHubAPIClient:
    def __init__(self, token: Optional[str] = None):
        self.token = token or os.getenv('GITHUB_TOKEN')
        if not self.token:
            raise ValueError("GitHub token required. Set GITHUB_TOKEN environment variable.")
        
        self.base_url = 'https://api.github.com'
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'token {self.token}',
            'Accept': 'application/vnd.github.v3+json'
        })
    
    def _make_request(self, method: str, endpoint: str, **kwargs) -> Any:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        response = self.session.request(method, url, **kwargs)
        response.raise_for_status()
        return response.json()
    
    def search_assigned_prs(self, title_prefix: str) -> List[Dict[str, Any]]:
        query = f'is:pr is:open assignee:@me'
        
        response = self._make_request('GET', 'search/issues', params={'q': query})
        
        # Filter by title prefix
        return [pr for pr in response.get('items', []) if pr['title'].startswith(title_prefix)]
    
    def get_pr_diff(self, pr_url: str) -> str:
        # Parse GitHub PR URL to extract owner, repo, and PR number
        # Expected format: https://github.com/owner/repo/pull/123
        parts = pr_url.rstrip('/').split('/')
        if len(parts) < 7 or 'github.com' not in pr_url or 'pull' not in parts:
            raise ValueError("Invalid GitHub PR URL format")
        
        owner = parts[-4]
        repo = parts[-3]
        pr_number = parts[-1]
        
        # Get PR diff using GitHub API
        endpoint = f'repos/{owner}/{repo}/pulls/{pr_number}'
        headers = {'Accept': 'application/vnd.github.v3.diff'}
        
        url = f"{self.base_url}/{endpoint}"
        response = self.session.get(url, headers={**self.session.headers, **headers})
        response.raise_for_status()
        
        return response.text
    
    def approve_pr(self, pr_url: str) -> None:
        # Parse GitHub PR URL to extract owner, repo, and PR number
        parts = pr_url.rstrip('/').split('/')
        if len(parts) < 7 or 'github.com' not in pr_url or 'pull' not in parts:
            raise ValueError("Invalid GitHub PR URL format")
        
        owner = parts[-4]
        repo = parts[-3]
        pr_number = parts[-1]
        
        # Create approval review
        endpoint = f'repos/{owner}/{repo}/pulls/{pr_number}/reviews'
        data = {
            'event': 'APPROVE',
            'body': 'Auto-approved by multimerger script (link soon)'
        }
        
        self._make_request('POST', endpoint, json=data)
    
    def merge_pr(self, pr_url: str, merge_method: str = 'squash') -> None:
        # Parse GitHub PR URL to extract owner, repo, and PR number
        parts = pr_url.rstrip('/').split('/')
        if len(parts) < 7 or 'github.com' not in pr_url or 'pull' not in parts:
            raise ValueError("Invalid GitHub PR URL format")
        
        owner = parts[-4]
        repo = parts[-3]
        pr_number = parts[-1]
        
        # Merge PR
        endpoint = f'repos/{owner}/{repo}/pulls/{pr_number}/merge'
        data = {
            'merge_method': merge_method
        }
        
        self._make_request('PUT', endpoint, json=data)


class PRMatcher:
    def __init__(self, client: GitHubAPIClient):
        self.client = client
    
    def find_matching_prs(self, assigned_prs: List[Dict[str, Any]], example_diff: str) -> List[Dict[str, Any]]:
        matching_prs = []
        
        for pr in assigned_prs:
            pr_diff = self.client.get_pr_diff(pr['html_url'])
            if pr_diff.strip() == example_diff.strip():
                matching_prs.append(pr)
        
        return matching_prs


def main():
    parser = argparse.ArgumentParser(
        description='Search for GitHub PRs assigned to you with a specific title prefix'
    )
    parser.add_argument(
        'title_prefix',
        help='Title prefix to search for in PR titles'
    )
    parser.add_argument(
        '--token',
        help='GitHub token (defaults to GITHUB_TOKEN environment variable)'
    )
    parser.add_argument(
        'example_pr',
        help='Example PR URL to match diffs against'
    )
    
    args = parser.parse_args()
    
    try:
        client = GitHubAPIClient(token=args.token)
        
        prs = client.search_assigned_prs(args.title_prefix)
        
        if not prs:
            print(f"No PRs assigned to you with title starting with '{args.title_prefix}'")
            return
        
        # Filter to only matching PRs
        example_diff = client.get_pr_diff(args.example_pr)
        
        print("Example PR diff:")
        print("-" * 60)
        print(colorize_diff(example_diff))
        print("-" * 60)
        
        confirmation = input("Continue with this diff? (y/N): ").strip().lower()
        if confirmation not in ['y', 'yes']:
            print("Aborted.")
            return
        
        print("Searching for matching PRs...")
        matcher = PRMatcher(client)
        prs = matcher.find_matching_prs(prs, example_diff)
        
        if not prs:
            print(f"No PRs with matching diff found")
            return
        
        print(f"Found {len(prs)} matching PRs:")
        for pr in prs:
            repo_owner, repo_name = pr['repository_url'].split('/')[-2:]
            # Use ANSI escape sequence to create clickable hyperlink: \033]8;;URL\033\TEXT\033]8;;\033\
            print(f"  \033]8;;{pr['html_url']}\033\\{repo_name} #{pr['number']}\033]8;;\033\\")
        
        # Process each PR with confirmation
        auto_approve_all = False
        
        for pr in prs:
            repo_owner, repo_name = pr['repository_url'].split('/')[-2:]
            # Use ANSI escape sequence to create clickable hyperlink
            print(f"\nProcess \033]8;;{pr['html_url']}\033\\{repo_name} #{pr['number']}\033]8;;\033\\?")
            
            if not auto_approve_all:
                confirmation = input("Approve and merge? (y/N/s=stop/a=all): ").strip().lower()
                
                if confirmation == 's':
                    print("Stopped processing.")
                    break
                elif confirmation == 'a':
                    auto_approve_all = True
                    print("Auto-approving all remaining PRs...")
                elif confirmation not in ['y', 'yes']:
                    print(f"Skipped {repo_name} #{pr['number']}")
                    continue
            
            try:
                print(f"Approving {repo_name} #{pr['number']}...")
                client.approve_pr(pr['html_url'])
                
                print(f"Merging {repo_name} #{pr['number']}...")
                client.merge_pr(pr['html_url'])
                
                print(f"✓ Successfully processed {repo_name} #{pr['number']}")
            except Exception as e:
                print(f"✗ Failed to process {repo_name} #{pr['number']}: {e}")
            
    except requests.exceptions.RequestException as e:
        print(f"API request failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
