"""
AWS Lambda Handlers for PDF Monitor

This package contains Lambda functions for:
- IDP Enrichment Pipeline (enrichment_handler.py)
  - Textract Forms/Tables/Queries/Signatures
  - Comprehend Classification/NER
  - A2I Human Review integration

All Lambda handlers are ADDITIVE features that do not
modify or replace existing processing pipelines.
"""
