from libs.paper_explanations.exceptions.claim_extraction_error import ClaimExtractionError


class ClaimExtractionRules:
    @staticmethod
    def request(snapshot_id, evidence):
        raise ClaimExtractionError("not_implemented")

    @staticmethod
    def parse(request, candidate):
        raise ClaimExtractionError("not_implemented")
