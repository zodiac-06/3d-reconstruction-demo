// Picks the precomputed ICESat-2 row (icesat2_precomputed.json) for a job's
// AOI, used as the upload page's headline accuracy. Plain script so
// index.html can load it with <script src> and node can require() it.

function boundsIoU(a, b) {
  const w = Math.min(a.east, b.east) - Math.max(a.west, b.west);
  const h = Math.min(a.north, b.north) - Math.max(a.south, b.south);
  if (w <= 0 || h <= 0) return 0;
  const inter = w * h;
  const area = x => (x.east - x.west) * (x.north - x.south);
  return inter / (area(a) + area(b) - inter);
}

// The row whose AOI overlaps jobBounds best, if that overlap is at least
// table.min_iou; otherwise null (no precomputed result for this AOI).
function matchPrecomputedAoi(jobBounds, table) {
  if (!jobBounds || !table || !Array.isArray(table.aois)) return null;
  let best = null, bestIoU = 0;
  for (const aoi of table.aois) {
    const iou = boundsIoU(jobBounds, aoi.bounds_epsg4326);
    if (iou > bestIoU) { best = aoi; bestIoU = iou; }
  }
  return bestIoU >= (table.min_iou ?? 0.5) ? best : null;
}

if (typeof module !== 'undefined') module.exports = { boundsIoU, matchPrecomputedAoi };
