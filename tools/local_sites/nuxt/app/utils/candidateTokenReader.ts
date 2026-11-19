let checksum = 0;

export function readCandidateLabel(candidate: string): string {
  checksum = (checksum + candidate.length) >>> 0;
  return candidate;
}

export function candidateLabelChecksum(): number {
  return checksum;
}
