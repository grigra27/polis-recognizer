"""Minimal example — extract structured fields from a single policy PDF."""

from polis_recognizer import PolicyExtractor


def main(pdf_path: str) -> None:
    extractor = PolicyExtractor()
    result = extractor.extract_from_pdf(pdf_path)

    print(f"Policy number:     {result.policy_number or '—'}")
    if result.policy_period:
        print(
            f"Policy period:     {result.policy_period['start']} → "
            f"{result.policy_period['end']}"
        )
    if result.franchise:
        if result.franchise.get("absent"):
            print(f"Franchise:         absent (no deductible)")
        else:
            print(
                f"Franchise:         {result.franchise['value']} "
                f"{result.franchise['currency']}"
            )
    if result.limit:
        print(f"Sum insured:       {result.limit['value']} {result.limit['currency']}")
    if result.premium:
        print(
            f"Premium:           {result.premium['value']} {result.premium['currency']}"
        )
    print(f"Sum type:          {result.sum_type or '—'}")
    print(f"Repair mode:       {result.repair_mode or '—'}")
    print()
    print(f"Extraction method: {result.extraction_method}")
    print(f"Status:            {result.extraction_status}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("Usage: python examples/basic_usage.py /path/to/policy.pdf")
        sys.exit(1)
    main(sys.argv[1])
