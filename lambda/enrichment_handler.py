"""
AWS Lambda Handler for IDP Enrichment Pipeline

This Lambda function processes PDF versions through the IDP enrichment
pipeline (Textract + Comprehend + optional A2I) asynchronously.

Trigger sources:
- S3 events (new PDF uploaded)
- SQS messages (enrichment requests)
- Direct invocation (from main app)
- EventBridge (scheduled enrichment)

This is an ADDITIVE feature that does not affect the main
processing pipeline. Enrichment runs after the existing
fetch/extract/hash/store flow completes.

Deployment:
- Package with dependencies (boto3, sqlalchemy, structlog)
- Configure environment variables (see env.example)
- Set appropriate IAM permissions for Textract, Comprehend, A2I
- Optional: Configure S3 trigger or SQS queue
"""

import json
import os
import sys
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime

# Add project root to path for local imports
# (In production Lambda, package as layer or include in deployment)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    AWS Lambda handler for IDP enrichment.
    
    Supports multiple event sources:
    - S3 events: Process new PDFs uploaded to enrichment bucket
    - SQS messages: Process enrichment requests from queue
    - Direct invocation: Process specific PDF version
    - EventBridge: Process pending enrichments on schedule
    
    Args:
        event: Lambda event payload
        context: Lambda context
        
    Returns:
        Response dict with processing results
    """
    import structlog
    
    # Configure logging
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer()
        ]
    )
    logger = structlog.get_logger()
    
    logger.info("Lambda enrichment handler invoked", event_keys=list(event.keys()))
    
    try:
        # Determine event source and route appropriately
        if 'Records' in event:
            # S3 or SQS event
            records = event['Records']
            if records and records[0].get('eventSource') == 'aws:s3':
                return handle_s3_event(event, context, logger)
            elif records and records[0].get('eventSource') == 'aws:sqs':
                return handle_sqs_event(event, context, logger)
        
        if 'detail-type' in event:
            # EventBridge scheduled event
            return handle_eventbridge_event(event, context, logger)
        
        if 'pdf_version_id' in event:
            # Direct invocation with specific version
            return handle_direct_invocation(event, context, logger)
        
        if 'action' in event:
            # Action-based invocation
            action = event['action']
            if action == 'process_pending':
                return handle_process_pending(event, context, logger)
            elif action == 'enrich_version':
                return handle_direct_invocation(event, context, logger)
        
        return {
            'statusCode': 400,
            'body': json.dumps({
                'error': 'Unknown event format',
                'event_keys': list(event.keys())
            })
        }
        
    except Exception as e:
        logger.error("Lambda handler error", error=str(e))
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': str(e)
            })
        }


def handle_direct_invocation(
    event: Dict[str, Any],
    context: Any,
    logger
) -> Dict[str, Any]:
    """
    Handle direct invocation with specific PDF version ID.
    
    Expected event format:
    {
        "pdf_version_id": 123,
        "pdf_path": "/path/to/pdf",  # Optional, will lookup if not provided
        "text_content": "...",  # Optional, will read if not provided
        "url": "https://example.com/form.pdf"  # Optional
    }
    """
    from config import settings
    from db.database import get_session
    from db.models import PDFVersion
    from services.idp_enrichment import get_idp_orchestrator
    
    pdf_version_id = event.get('pdf_version_id')
    if not pdf_version_id:
        return {
            'statusCode': 400,
            'body': json.dumps({'error': 'pdf_version_id is required'})
        }
    
    logger.info("Processing direct invocation", version_id=pdf_version_id)
    
    # Get database session
    with get_session() as session:
        # Lookup PDF version
        version = session.query(PDFVersion).filter_by(id=pdf_version_id).first()
        if not version:
            return {
                'statusCode': 404,
                'body': json.dumps({'error': f'PDFVersion {pdf_version_id} not found'})
            }
        
        # Get PDF path
        pdf_path = event.get('pdf_path')
        if not pdf_path:
            pdf_path = settings.PDF_STORAGE_PATH / version.normalized_pdf_path
        else:
            pdf_path = Path(pdf_path)
        
        # Get text content
        text_content = event.get('text_content', '')
        if not text_content and version.extracted_text_path:
            text_path = settings.PDF_STORAGE_PATH / version.extracted_text_path
            if text_path.exists():
                text_content = text_path.read_text()
        
        # Get URL
        url = event.get('url', '')
        if not url and version.monitored_url:
            url = version.monitored_url.url
        
        # Run enrichment
        orchestrator = get_idp_orchestrator()
        result = orchestrator.process_version(
            pdf_version_id=pdf_version_id,
            pdf_path=pdf_path,
            text_content=text_content,
            url=url
        )
        
        # Persist results
        if result.success:
            orchestrator.persist_enrichment(pdf_version_id, result, session)
        
        return {
            'statusCode': 200,
            'body': json.dumps({
                'success': result.success,
                'pdf_version_id': pdf_version_id,
                'document_type': result.comprehend_document_type,
                'form_number': result.extracted_form_number,
                'revision_date': result.extracted_revision_date,
                'features_used': result.features_used,
                'a2i_submitted': result.a2i_submitted,
                'error': result.error
            })
        }


def handle_s3_event(
    event: Dict[str, Any],
    context: Any,
    logger
) -> Dict[str, Any]:
    """
    Handle S3 event for new PDF upload.
    
    Expected: PDF uploaded to enrichment S3 bucket with metadata
    containing pdf_version_id.
    """
    import boto3
    from config import settings
    from db.database import get_session
    from db.models import PDFVersion
    from services.idp_enrichment import get_idp_orchestrator
    
    results = []
    s3_client = boto3.client('s3')
    
    for record in event.get('Records', []):
        bucket = record['s3']['bucket']['name']
        key = record['s3']['object']['key']
        
        logger.info("Processing S3 object", bucket=bucket, key=key)
        
        try:
            # Get object metadata
            head_response = s3_client.head_object(Bucket=bucket, Key=key)
            metadata = head_response.get('Metadata', {})
            
            pdf_version_id = metadata.get('pdf_version_id')
            if not pdf_version_id:
                logger.warning("No pdf_version_id in metadata", key=key)
                results.append({'key': key, 'error': 'No pdf_version_id in metadata'})
                continue
            
            # Download PDF to temp location
            import tempfile
            with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
                s3_client.download_fileobj(bucket, key, tmp)
                pdf_path = Path(tmp.name)
            
            # Process using direct invocation logic
            sub_event = {
                'pdf_version_id': int(pdf_version_id),
                'pdf_path': str(pdf_path),
                'url': metadata.get('url', '')
            }
            sub_result = handle_direct_invocation(sub_event, context, logger)
            results.append({
                'key': key,
                'pdf_version_id': pdf_version_id,
                'result': json.loads(sub_result['body'])
            })
            
            # Clean up temp file
            pdf_path.unlink(missing_ok=True)
            
        except Exception as e:
            logger.error("Error processing S3 object", key=key, error=str(e))
            results.append({'key': key, 'error': str(e)})
    
    return {
        'statusCode': 200,
        'body': json.dumps({
            'processed': len(results),
            'results': results
        })
    }


def handle_sqs_event(
    event: Dict[str, Any],
    context: Any,
    logger
) -> Dict[str, Any]:
    """
    Handle SQS messages for enrichment requests.
    
    Expected message format:
    {
        "pdf_version_id": 123,
        "url": "https://example.com/form.pdf"
    }
    """
    results = []
    
    for record in event.get('Records', []):
        try:
            body = json.loads(record['body'])
            logger.info("Processing SQS message", message_id=record['messageId'])
            
            sub_result = handle_direct_invocation(body, context, logger)
            results.append({
                'message_id': record['messageId'],
                'result': json.loads(sub_result['body'])
            })
            
        except Exception as e:
            logger.error(
                "Error processing SQS message",
                message_id=record.get('messageId'),
                error=str(e)
            )
            results.append({
                'message_id': record.get('messageId'),
                'error': str(e)
            })
    
    return {
        'statusCode': 200,
        'body': json.dumps({
            'processed': len(results),
            'results': results
        })
    }


def handle_eventbridge_event(
    event: Dict[str, Any],
    context: Any,
    logger
) -> Dict[str, Any]:
    """
    Handle EventBridge scheduled event.
    
    Processes all pending enrichments on a schedule.
    """
    logger.info("Processing EventBridge scheduled event")
    return handle_process_pending(event, context, logger)


def handle_process_pending(
    event: Dict[str, Any],
    context: Any,
    logger
) -> Dict[str, Any]:
    """
    Process all PDF versions pending enrichment.
    
    Finds versions where idp_enrichment_status is NULL or 'pending'
    and processes them through the enrichment pipeline.
    """
    from config import settings
    from db.database import get_session
    from db.models import PDFVersion
    from services.idp_enrichment import get_idp_orchestrator
    
    # Configurable batch size
    batch_size = event.get('batch_size', 10)
    
    logger.info("Processing pending enrichments", batch_size=batch_size)
    
    results = []
    
    with get_session() as session:
        # Find versions pending enrichment
        pending_versions = session.query(PDFVersion).filter(
            (PDFVersion.idp_enrichment_status == None) |  # noqa: E711
            (PDFVersion.idp_enrichment_status == 'pending')
        ).limit(batch_size).all()
        
        logger.info("Found pending versions", count=len(pending_versions))
        
        orchestrator = get_idp_orchestrator()
        
        for version in pending_versions:
            try:
                # Mark as processing
                version.idp_enrichment_status = 'processing'
                session.commit()
                
                # Get PDF path and text content
                pdf_path = settings.PDF_STORAGE_PATH / version.normalized_pdf_path
                text_content = ''
                if version.extracted_text_path:
                    text_path = settings.PDF_STORAGE_PATH / version.extracted_text_path
                    if text_path.exists():
                        text_content = text_path.read_text()
                
                url = version.monitored_url.url if version.monitored_url else ''
                
                # Run enrichment
                result = orchestrator.process_version(
                    pdf_version_id=version.id,
                    pdf_path=pdf_path,
                    text_content=text_content,
                    url=url
                )
                
                # Persist results
                orchestrator.persist_enrichment(version.id, result, session)
                
                results.append({
                    'pdf_version_id': version.id,
                    'success': result.success,
                    'document_type': result.comprehend_document_type,
                    'error': result.error
                })
                
            except Exception as e:
                logger.error(
                    "Error processing version",
                    version_id=version.id,
                    error=str(e)
                )
                version.idp_enrichment_status = 'failed'
                version.idp_enrichment_error = str(e)
                session.commit()
                results.append({
                    'pdf_version_id': version.id,
                    'success': False,
                    'error': str(e)
                })
    
    return {
        'statusCode': 200,
        'body': json.dumps({
            'processed': len(results),
            'results': results
        })
    }


# For local testing
if __name__ == '__main__':
    # Test direct invocation
    test_event = {
        'action': 'process_pending',
        'batch_size': 5
    }
    
    result = handler(test_event, None)
    print(json.dumps(json.loads(result['body']), indent=2))
