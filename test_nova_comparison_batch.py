#!/usr/bin/env python3
"""
Batch comparison of Nova 2 Lite vs Textract+Claude on all PDFs in the database.

Usage:
    python test_nova_comparison_batch.py              # Test all PDFs
    python test_nova_comparison_batch.py --limit 10  # Test first 10 PDFs
    python test_nova_comparison_batch.py --dry-run   # Show which PDFs would be tested

Output:
    - comparison_report.md: Markdown report with differences and stats
    - comparison_results.json: Detailed JSON results
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

import structlog

from db.database import SessionLocal
from db.models import PDFVersion, MonitoredURL
from storage.file_store import FileStore
from storage.version_manager import VersionManager

logger = structlog.get_logger()


def get_all_pdf_versions(limit: Optional[int] = None) -> List[Dict]:
    """Get all PDF versions with their file paths."""
    db = SessionLocal()
    file_store = FileStore()
    version_manager = VersionManager(file_store)
    
    try:
        query = db.query(PDFVersion, MonitoredURL).join(
            MonitoredURL, PDFVersion.monitored_url_id == MonitoredURL.id
        ).order_by(PDFVersion.id)
        
        if limit:
            query = query.limit(limit)
        
        results = []
        for version, url in query.all():
            pdf_path = version_manager.get_original_pdf_path(db, version.id)
            if pdf_path and pdf_path.exists():
                results.append({
                    "version_id": version.id,
                    "url_id": url.id,
                    "url_name": url.name,
                    "url": url.url,
                    "pdf_path": pdf_path,
                    "existing_title": version.formatted_title,
                    "existing_form_number": version.form_number,
                    "file_size_kb": pdf_path.stat().st_size / 1024
                })
        
        return results
    finally:
        db.close()


def test_textract_claude(pdf_path: Path) -> Dict:
    """Test Textract + Claude workflow."""
    from services.title_extractor import TitleExtractor
    
    extractor = TitleExtractor()
    if not extractor.is_available():
        return {
            "success": False,
            "error": "Not available"
        }
    
    start = time.time()
    try:
        result = extractor.extract_title(pdf_path)
        elapsed = time.time() - start
        
        return {
            "success": result.success,
            "title": result.formatted_title,
            "form_number": result.form_number,
            "revision_date": result.revision_date,
            "confidence": result.combined_confidence,
            "time_seconds": round(elapsed, 2),
            "error": result.error
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "time_seconds": round(time.time() - start, 2)
        }


def test_nova(pdf_path: Path) -> Dict:
    """Test Nova 2 Lite workflow."""
    # Force enable Nova for testing
    os.environ["BEDROCK_NOVA_ENABLED"] = "True"
    
    from services.nova_document_processor import NovaDocumentProcessor
    
    processor = NovaDocumentProcessor()
    processor._available = None  # Reset to pick up env var
    
    if not processor.is_available():
        return {
            "success": False,
            "error": "Not available"
        }
    
    start = time.time()
    try:
        result = processor.extract_title_and_form(pdf_path)
        elapsed = time.time() - start
        
        return {
            "success": result.success,
            "title": result.formatted_title,
            "form_number": result.form_number,
            "revision_date": result.revision_date,
            "confidence": result.combined_confidence,
            "time_seconds": round(elapsed, 2),
            "error": result.error
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "time_seconds": round(time.time() - start, 2)
        }


def run_comparison(versions: List[Dict], output_file: Optional[str] = None) -> Dict:
    """Run comparison on all versions and collect statistics."""
    
    results = []
    stats = {
        "total": len(versions),
        "textract_claude_success": 0,
        "nova_success": 0,
        "both_success": 0,
        "titles_match": 0,
        "form_numbers_match": 0,
        "nova_faster_count": 0,
        "textract_claude_total_time": 0,
        "nova_total_time": 0,
    }
    
    print(f"\n{'='*80}")
    print(f"BATCH COMPARISON: Nova 2 Lite vs Textract+Claude")
    print(f"{'='*80}")
    print(f"Testing {len(versions)} PDFs...")
    print(f"{'='*80}\n")
    
    for i, version in enumerate(versions, 1):
        pdf_path = version["pdf_path"]
        print(f"[{i}/{len(versions)}] Testing: {version['url_name'][:50]}...")
        print(f"         PDF: {pdf_path}")
        
        # Test both methods
        tc_result = test_textract_claude(pdf_path)
        nova_result = test_nova(pdf_path)
        
        # Collect result
        result = {
            "version_id": version["version_id"],
            "url_id": version["url_id"],
            "url_name": version["url_name"],
            "pdf_path": str(pdf_path),
            "file_size_kb": version["file_size_kb"],
            "textract_claude": tc_result,
            "nova": nova_result
        }
        results.append(result)
        
        # Update stats
        tc_success = tc_result.get("success", False)
        nova_success = nova_result.get("success", False)
        
        if tc_success:
            stats["textract_claude_success"] += 1
            stats["textract_claude_total_time"] += tc_result.get("time_seconds", 0)
        
        if nova_success:
            stats["nova_success"] += 1
            stats["nova_total_time"] += nova_result.get("time_seconds", 0)
        
        if tc_success and nova_success:
            stats["both_success"] += 1
            
            if tc_result.get("title") == nova_result.get("title"):
                stats["titles_match"] += 1
            
            if tc_result.get("form_number") == nova_result.get("form_number"):
                stats["form_numbers_match"] += 1
            
            tc_time = tc_result.get("time_seconds", 0)
            nova_time = nova_result.get("time_seconds", 0)
            if nova_time > 0 and tc_time > nova_time:
                stats["nova_faster_count"] += 1
        
        # Print quick result
        status = ""
        if tc_success and nova_success:
            match = "✓" if tc_result.get("title") == nova_result.get("title") else "✗"
            status = f"Both OK | Match: {match} | TC: {tc_result.get('time_seconds')}s, Nova: {nova_result.get('time_seconds')}s"
        elif nova_success:
            status = f"Nova OK, TC failed: {tc_result.get('error', 'unknown')}"
        elif tc_success:
            status = f"TC OK, Nova failed: {nova_result.get('error', 'unknown')}"
        else:
            status = f"Both failed"
        
        print(f"         Result: {status}\n")
    
    # Calculate averages
    if stats["textract_claude_success"] > 0:
        stats["textract_claude_avg_time"] = round(
            stats["textract_claude_total_time"] / stats["textract_claude_success"], 2
        )
    else:
        stats["textract_claude_avg_time"] = 0
    
    if stats["nova_success"] > 0:
        stats["nova_avg_time"] = round(
            stats["nova_total_time"] / stats["nova_success"], 2
        )
    else:
        stats["nova_avg_time"] = 0
    
    # Save results to file
    if output_file:
        output_path = Path(output_file)
        with open(output_path, 'w') as f:
            json.dump({
                "timestamp": datetime.now().isoformat(),
                "stats": stats,
                "results": results
            }, f, indent=2, default=str)
        print(f"Detailed results saved to: {output_path}")
    
    return {"stats": stats, "results": results}


def generate_markdown_report(stats: Dict, results: List[Dict], output_path: str):
    """Generate a markdown report with differences and statistics."""
    
    lines = []
    lines.append("# Nova 2 Lite vs Textract+Claude Comparison Report")
    lines.append("")
    lines.append(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    
    # Overall Statistics
    lines.append("## Overall Statistics")
    lines.append("")
    
    total = stats["total"]
    both = stats["both_success"]
    
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Total PDFs Tested | {total} |")
    lines.append(f"| Textract+Claude Success | {stats['textract_claude_success']}/{total} ({100*stats['textract_claude_success']/total:.1f}%) |")
    lines.append(f"| Nova 2 Lite Success | {stats['nova_success']}/{total} ({100*stats['nova_success']/total:.1f}%) |")
    lines.append(f"| Both Succeeded | {both}/{total} ({100*both/total:.1f}%) |")
    lines.append("")
    
    # Accuracy Stats
    if both > 0:
        lines.append("## Accuracy (when both succeeded)")
        lines.append("")
        lines.append(f"| Metric | Value |")
        lines.append(f"|--------|-------|")
        lines.append(f"| Titles Match | {stats['titles_match']}/{both} ({100*stats['titles_match']/both:.1f}%) |")
        lines.append(f"| Form Numbers Match | {stats['form_numbers_match']}/{both} ({100*stats['form_numbers_match']/both:.1f}%) |")
        lines.append("")
    
    # Performance Stats
    lines.append("## Performance")
    lines.append("")
    lines.append(f"| Metric | Textract+Claude | Nova 2 Lite |")
    lines.append(f"|--------|-----------------|-------------|")
    lines.append(f"| Average Time | {stats['textract_claude_avg_time']}s | {stats['nova_avg_time']}s |")
    lines.append(f"| Total Time | {stats['textract_claude_total_time']:.1f}s | {stats['nova_total_time']:.1f}s |")
    
    if stats["nova_avg_time"] > 0:
        speedup = stats["textract_claude_avg_time"] / stats["nova_avg_time"]
        lines.append(f"| **Speedup** | - | **{speedup:.2f}x {'faster' if speedup > 1 else 'slower'}** |")
    
    if both > 0:
        lines.append(f"| Nova Faster In | - | {stats['nova_faster_count']}/{both} ({100*stats['nova_faster_count']/both:.1f}%) |")
    lines.append("")
    
    # Confidence Stats
    tc_confidences = []
    nova_confidences = []
    for r in results:
        tc = r.get("textract_claude", {})
        nova = r.get("nova", {})
        if tc.get("success") and tc.get("confidence"):
            tc_confidences.append(tc["confidence"])
        if nova.get("success") and nova.get("confidence"):
            nova_confidences.append(nova["confidence"])
    
    if tc_confidences or nova_confidences:
        lines.append("## Confidence Scores")
        lines.append("")
        lines.append(f"| Metric | Textract+Claude | Nova 2 Lite |")
        lines.append(f"|--------|-----------------|-------------|")
        if tc_confidences:
            tc_avg = sum(tc_confidences) / len(tc_confidences)
            tc_min = min(tc_confidences)
            tc_max = max(tc_confidences)
            nova_avg = f"{sum(nova_confidences)/len(nova_confidences):.3f}" if nova_confidences else "N/A"
            nova_min = f"{min(nova_confidences):.3f}" if nova_confidences else "N/A"
            nova_max = f"{max(nova_confidences):.3f}" if nova_confidences else "N/A"
            lines.append(f"| Average Confidence | {tc_avg:.3f} | {nova_avg} |")
            lines.append(f"| Min Confidence | {tc_min:.3f} | {nova_min} |")
            lines.append(f"| Max Confidence | {tc_max:.3f} | {nova_max} |")
        lines.append("")
    
    # Differences Section
    differences = []
    for r in results:
        tc = r.get("textract_claude", {})
        nova = r.get("nova", {})
        
        if tc.get("success") and nova.get("success"):
            title_diff = tc.get("title") != nova.get("title")
            form_diff = tc.get("form_number") != nova.get("form_number")
            
            if title_diff or form_diff:
                differences.append({
                    "version_id": r["version_id"],
                    "url_id": r["url_id"],
                    "url_name": r["url_name"],
                    "pdf_path": str(r.get("pdf_path", "")),
                    "file_size_kb": r["file_size_kb"],
                    "title_diff": title_diff,
                    "form_diff": form_diff,
                    "tc_title": tc.get("title", ""),
                    "nova_title": nova.get("title", ""),
                    "tc_form": tc.get("form_number", ""),
                    "nova_form": nova.get("form_number", ""),
                    "tc_confidence": tc.get("confidence", 0),
                    "nova_confidence": nova.get("confidence", 0),
                })
    
    if differences:
        lines.append("## Differences Found")
        lines.append("")
        lines.append(f"**{len(differences)} PDFs had different results:**")
        lines.append("")
        
        for i, diff in enumerate(differences, 1):
            lines.append(f"### {i}. {diff['url_name']}")
            lines.append("")
            lines.append(f"- **Version ID:** {diff['version_id']}")
            lines.append(f"- **File Size:** {diff['file_size_kb']:.1f} KB")
            lines.append(f"- **PDF Path:** `{diff['pdf_path']}`")
            lines.append("")
            
            if diff["title_diff"]:
                lines.append("**Title Difference:**")
                lines.append(f"- Textract+Claude: `{diff['tc_title']}`")
                lines.append(f"- Nova 2 Lite: `{diff['nova_title']}`")
                lines.append("")
            
            if diff["form_diff"]:
                lines.append("**Form Number Difference:**")
                lines.append(f"- Textract+Claude: `{diff['tc_form']}`")
                lines.append(f"- Nova 2 Lite: `{diff['nova_form']}`")
                lines.append("")
            
            lines.append(f"**Confidence:** TC={diff['tc_confidence']:.3f}, Nova={diff['nova_confidence']:.3f}")
            lines.append("")
            lines.append("---")
            lines.append("")
    else:
        lines.append("## Differences Found")
        lines.append("")
        lines.append("**No differences found!** All titles and form numbers matched.")
        lines.append("")
    
    # Failures Section
    tc_failures = []
    nova_failures = []
    for r in results:
        tc = r.get("textract_claude", {})
        nova = r.get("nova", {})
        
        if not tc.get("success"):
            tc_failures.append({
                "url_name": r["url_name"],
                "version_id": r["version_id"],
                "pdf_path": r.get("pdf_path", ""),
                "error": tc.get("error", "Unknown")
            })
        if not nova.get("success"):
            nova_failures.append({
                "url_name": r["url_name"],
                "version_id": r["version_id"],
                "pdf_path": r.get("pdf_path", ""),
                "error": nova.get("error", "Unknown")
            })
    
    if tc_failures or nova_failures:
        lines.append("## Failures")
        lines.append("")
        
        if tc_failures:
            lines.append(f"### Textract+Claude Failures ({len(tc_failures)})")
            lines.append("")
            for f in tc_failures:
                lines.append(f"**{f['url_name']}** (Version {f['version_id']})")
                lines.append(f"- Path: `{f['pdf_path']}`")
                lines.append(f"- Error: {f['error']}")
                lines.append("")
        
        if nova_failures:
            lines.append(f"### Nova 2 Lite Failures ({len(nova_failures)})")
            lines.append("")
            for f in nova_failures:
                lines.append(f"**{f['url_name']}** (Version {f['version_id']})")
                lines.append(f"- Path: `{f['pdf_path']}`")
                lines.append(f"- Error: {f['error']}")
                lines.append("")
    
    # All Results Table
    lines.append("## All Results")
    lines.append("")
    lines.append("| Version ID | URL Name | TC Title | Nova Title | TC Form | Nova Form | TC Time | Nova Time | Match |")
    lines.append("|------------|----------|----------|------------|---------|-----------|---------|-----------|-------|")
    
    for r in results:
        tc = r.get("textract_claude", {})
        nova = r.get("nova", {})
        
        tc_title = (tc.get("title") or "")[:30]
        nova_title = (nova.get("title") or "")[:30]
        tc_form = tc.get("form_number") or "-"
        nova_form = nova.get("form_number") or "-"
        tc_time = f"{tc.get('time_seconds', '-')}s" if tc.get("success") else "FAIL"
        nova_time = f"{nova.get('time_seconds', '-')}s" if nova.get("success") else "FAIL"
        
        match = "✓" if (tc.get("title") == nova.get("title") and tc.get("form_number") == nova.get("form_number")) else "✗"
        if not (tc.get("success") and nova.get("success")):
            match = "-"
        
        url_name = r["url_name"][:25]
        lines.append(f"| {r['version_id']} | {url_name} | {tc_title} | {nova_title} | {tc_form} | {nova_form} | {tc_time} | {nova_time} | {match} |")
    
    lines.append("")
    
    # Recommendation
    lines.append("## Recommendation")
    lines.append("")
    
    if both > 0:
        match_rate = stats["titles_match"] / both
        speedup = stats["textract_claude_avg_time"] / stats["nova_avg_time"] if stats["nova_avg_time"] > 0 else 1
        
        if match_rate >= 0.95 and speedup > 1:
            lines.append("**✓ Strongly Recommend Nova 2 Lite** - Faster with excellent accuracy (95%+ match rate)")
        elif match_rate >= 0.9 and speedup > 1:
            lines.append("**✓ Recommend Nova 2 Lite** - Faster with high accuracy (90%+ match rate)")
        elif match_rate >= 0.8:
            lines.append("**~ Consider Nova 2 Lite** - Good accuracy but review the differences above")
        else:
            lines.append("**⚠ Review Required** - Significant differences found, manual review recommended")
    else:
        lines.append("**⚠ Insufficient Data** - Not enough successful comparisons to make a recommendation")
    
    lines.append("")
    
    # Write file
    with open(output_path, 'w') as f:
        f.write('\n'.join(lines))
    
    print(f"Markdown report saved to: {output_path}")


def print_summary(stats: Dict):
    """Print summary statistics."""
    print(f"\n{'='*80}")
    print("SUMMARY STATISTICS")
    print(f"{'='*80}\n")
    
    total = stats["total"]
    
    print(f"Total PDFs tested: {total}")
    print()
    
    print("SUCCESS RATES:")
    print(f"  Textract+Claude: {stats['textract_claude_success']}/{total} ({100*stats['textract_claude_success']/total:.1f}%)")
    print(f"  Nova 2 Lite:     {stats['nova_success']}/{total} ({100*stats['nova_success']/total:.1f}%)")
    print(f"  Both succeeded:  {stats['both_success']}/{total} ({100*stats['both_success']/total:.1f}%)")
    print()
    
    if stats["both_success"] > 0:
        both = stats["both_success"]
        print("ACCURACY (when both succeeded):")
        print(f"  Titles match:       {stats['titles_match']}/{both} ({100*stats['titles_match']/both:.1f}%)")
        print(f"  Form numbers match: {stats['form_numbers_match']}/{both} ({100*stats['form_numbers_match']/both:.1f}%)")
        print()
        
        print("PERFORMANCE:")
        print(f"  Textract+Claude avg time: {stats['textract_claude_avg_time']}s")
        print(f"  Nova 2 Lite avg time:     {stats['nova_avg_time']}s")
        
        if stats["nova_avg_time"] > 0:
            speedup = stats["textract_claude_avg_time"] / stats["nova_avg_time"]
            print(f"  Nova speedup:             {speedup:.2f}x {'faster' if speedup > 1 else 'slower'}")
        
        print(f"  Nova faster in:           {stats['nova_faster_count']}/{both} cases ({100*stats['nova_faster_count']/both:.1f}%)")
    
    print()
    print(f"TOTAL TIME:")
    print(f"  Textract+Claude: {stats['textract_claude_total_time']:.1f}s")
    print(f"  Nova 2 Lite:     {stats['nova_total_time']:.1f}s")
    
    print(f"\n{'='*80}")
    
    # Recommendation
    if stats["both_success"] > 0:
        both = stats["both_success"]
        match_rate = stats["titles_match"] / both
        speedup = stats["textract_claude_avg_time"] / stats["nova_avg_time"] if stats["nova_avg_time"] > 0 else 1
        
        if match_rate >= 0.9 and speedup > 1:
            print("✓ RECOMMENDATION: Switch to Nova 2 Lite - faster with high accuracy")
        elif match_rate >= 0.8:
            print("~ RECOMMENDATION: Nova 2 Lite is viable - review mismatches")
        else:
            print("⚠ RECOMMENDATION: Review differences before switching")
    
    print(f"{'='*80}\n")


def main():
    parser = argparse.ArgumentParser(description="Batch compare Nova vs Textract+Claude")
    parser.add_argument("--limit", type=int, help="Limit number of PDFs to test")
    parser.add_argument("--dry-run", action="store_true", help="Show PDFs without testing")
    parser.add_argument("--output", "-o", type=str, default="comparison_results.json",
                        help="Output file for detailed JSON results (default: comparison_results.json)")
    parser.add_argument("--report", "-r", type=str, default="comparison_report.md",
                        help="Output file for markdown report (default: comparison_report.md)")
    
    args = parser.parse_args()
    
    # Get all PDF versions
    print("Loading PDF versions from database...")
    versions = get_all_pdf_versions(limit=args.limit)
    
    if not versions:
        print("No PDFs found in database!")
        sys.exit(1)
    
    print(f"Found {len(versions)} PDFs")
    
    if args.dry_run:
        print("\nDRY RUN - PDFs that would be tested:")
        print("-" * 80)
        for v in versions:
            print(f"  [{v['version_id']}] {v['url_name'][:60]}")
            print(f"       {v['pdf_path']} ({v['file_size_kb']:.1f} KB)")
        print(f"\nTotal: {len(versions)} PDFs")
        sys.exit(0)
    
    # Run comparison
    result = run_comparison(versions, output_file=args.output)
    
    # Generate markdown report
    generate_markdown_report(result["stats"], result["results"], args.report)
    
    # Print summary
    print_summary(result["stats"])


if __name__ == "__main__":
    main()

