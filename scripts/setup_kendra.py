#!/usr/bin/env python3
"""
AWS Kendra Setup Script

Creates and configures an AWS Kendra index for the PDF Monitor system.
This script helps set up the initial Kendra index with proper configuration.

Usage:
    python scripts/setup_kendra.py create-index --name "PDF Monitor Index"
    python scripts/setup_kendra.py list-indexes
    python scripts/setup_kendra.py describe-index --index-id <index-id>
"""

import argparse
import sys
import os
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import boto3
from botocore.exceptions import ClientError
from config import settings


def create_index(name: str, description: str = None, edition: str = "DEVELOPER_EDITION"):
    """
    Create a new Kendra index.
    
    Args:
        name: Index name
        description: Optional description
        edition: Index edition (DEVELOPER_EDITION or ENTERPRISE_EDITION)
    """
    try:
        # Use default credential chain (SSO, IAM role, etc.) - same as AWS CLI
        # Only use explicit credentials if they're set AND non-empty
        client_kwargs = {'service_name': 'kendra', 'region_name': settings.AWS_REGION}
        if (settings.AWS_ACCESS_KEY_ID and settings.AWS_ACCESS_KEY_ID.strip() and 
            settings.AWS_SECRET_ACCESS_KEY and settings.AWS_SECRET_ACCESS_KEY.strip()):
            # Only use explicit credentials if they look valid (non-empty)
            client_kwargs['aws_access_key_id'] = settings.AWS_ACCESS_KEY_ID
            client_kwargs['aws_secret_access_key'] = settings.AWS_SECRET_ACCESS_KEY
        # Otherwise, boto3 will use default credential chain (SSO, IAM role, etc.)
        
        client = boto3.client(**client_kwargs)
        
        print(f"Creating Kendra index: {name}")
        print(f"Edition: {edition}")
        print(f"Region: {settings.AWS_REGION}")
        
        response = client.create_index(
            Name=name,
            Description=description or f"PDF Monitor index for court forms",
            Edition=edition,
            RoleArn=None  # Will use default service role
        )
        
        index_id = response['Id']
        print(f"\n✓ Index created successfully!")
        print(f"  Index ID: {index_id}")
        print(f"\nAdd this to your .env file:")
        print(f"  AWS_KENDRA_INDEX_ID={index_id}")
        print(f"\nNote: It may take a few minutes for the index to be ready.")
        print(f"Check status with: python scripts/setup_kendra.py describe-index --index-id {index_id}")
        
        return True
        
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', 'Unknown')
        error_msg = e.response.get('Error', {}).get('Message', str(e))
        print(f"ERROR: Failed to create index ({error_code}): {error_msg}")
        return False
    except Exception as e:
        print(f"ERROR: Unexpected error: {str(e)}")
        return False


def list_indexes():
    """List all Kendra indexes in the account."""
    try:
        # Use default credential chain (SSO, IAM role, etc.) - same as AWS CLI
        # Only use explicit credentials if they're set AND non-empty
        client_kwargs = {'service_name': 'kendra', 'region_name': settings.AWS_REGION}
        if (settings.AWS_ACCESS_KEY_ID and settings.AWS_ACCESS_KEY_ID.strip() and 
            settings.AWS_SECRET_ACCESS_KEY and settings.AWS_SECRET_ACCESS_KEY.strip()):
            # Only use explicit credentials if they look valid (non-empty)
            client_kwargs['aws_access_key_id'] = settings.AWS_ACCESS_KEY_ID
            client_kwargs['aws_secret_access_key'] = settings.AWS_SECRET_ACCESS_KEY
        # Otherwise, boto3 will use default credential chain (SSO, IAM role, etc.)
        
        client = boto3.client(**client_kwargs)
        
        response = client.list_indices()
        
        indexes = response.get('IndexConfigurationSummaryItems', [])
        
        if not indexes:
            print("No Kendra indexes found in this account/region.")
            return True
        
        print(f"Found {len(indexes)} Kendra index(es):\n")
        print(f"{'Index ID':<40} {'Name':<30} {'Status':<15} {'Edition':<20}")
        print("-" * 105)
        
        for index in indexes:
            index_id = index.get('Id', 'N/A')
            name = index.get('Name', 'N/A')
            status = index.get('Status', 'UNKNOWN')
            edition = index.get('Edition', 'N/A')
            print(f"{index_id:<40} {name:<30} {status:<15} {edition:<20}")
        
        return True
        
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', 'Unknown')
        error_msg = e.response.get('Error', {}).get('Message', str(e))
        print(f"ERROR: Failed to list indexes ({error_code}): {error_msg}")
        return False
    except Exception as e:
        print(f"ERROR: Unexpected error: {str(e)}")
        return False


def describe_index(index_id: str):
    """Describe a specific Kendra index."""
    try:
        # Use default credential chain (SSO, IAM role, etc.) - same as AWS CLI
        # Only use explicit credentials if they're set AND non-empty
        client_kwargs = {'service_name': 'kendra', 'region_name': settings.AWS_REGION}
        if (settings.AWS_ACCESS_KEY_ID and settings.AWS_ACCESS_KEY_ID.strip() and 
            settings.AWS_SECRET_ACCESS_KEY and settings.AWS_SECRET_ACCESS_KEY.strip()):
            # Only use explicit credentials if they look valid (non-empty)
            client_kwargs['aws_access_key_id'] = settings.AWS_ACCESS_KEY_ID
            client_kwargs['aws_secret_access_key'] = settings.AWS_SECRET_ACCESS_KEY
        # Otherwise, boto3 will use default credential chain (SSO, IAM role, etc.)
        
        client = boto3.client(**client_kwargs)
        
        response = client.describe_index(Id=index_id)
        
        print(f"Kendra Index Details")
        print("=" * 60)
        print(f"Index ID:      {response.get('Id', 'N/A')}")
        print(f"Name:          {response.get('Name', 'N/A')}")
        print(f"Status:        {response.get('Status', 'UNKNOWN')}")
        print(f"Edition:       {response.get('Edition', 'N/A')}")
        print(f"Created:       {response.get('CreatedAt', 'N/A')}")
        print(f"Updated:       {response.get('UpdatedAt', 'N/A')}")
        print(f"Document Count: {response.get('DocumentMetadataConfigurations', [])}")
        
        return True
        
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', 'Unknown')
        error_msg = e.response.get('Error', {}).get('Message', str(e))
        print(f"ERROR: Failed to describe index ({error_code}): {error_msg}")
        return False
    except Exception as e:
        print(f"ERROR: Unexpected error: {str(e)}")
        return False


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="AWS Kendra Setup Script",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")
    
    # Create index command
    create_parser = subparsers.add_parser("create-index", help="Create a new Kendra index")
    create_parser.add_argument("--name", required=True, help="Index name")
    create_parser.add_argument("--description", help="Index description")
    create_parser.add_argument(
        "--edition",
        choices=["DEVELOPER_EDITION", "ENTERPRISE_EDITION"],
        default="DEVELOPER_EDITION",
        help="Index edition (default: DEVELOPER_EDITION)"
    )
    
    # List indexes command
    subparsers.add_parser("list-indexes", help="List all Kendra indexes")
    
    # Describe index command
    describe_parser = subparsers.add_parser("describe-index", help="Describe a Kendra index")
    describe_parser.add_argument("--index-id", required=True, help="Index ID")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    if args.command == "create-index":
        success = create_index(
            name=args.name,
            description=args.description,
            edition=args.edition
        )
        sys.exit(0 if success else 1)
    elif args.command == "list-indexes":
        success = list_indexes()
        sys.exit(0 if success else 1)
    elif args.command == "describe-index":
        success = describe_index(index_id=args.index_id)
        sys.exit(0 if success else 1)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
