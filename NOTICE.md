# Acknowledgements & Third-Party Notices

This project's own code is licensed under the MIT License (see `LICENSE`).
It builds on the following third-party models, data, and libraries, each
under its own license/terms:

## Depth Anything V2

Monocular relative-depth estimation (the `vits` checkpoint) is provided by
[Depth Anything V2](https://github.com/DepthAnything/Depth-Anything-V2),
licensed under the **Apache License, Version 2.0**
(https://www.apache.org/licenses/LICENSE-2.0). A vendored copy of the
Depth-Anything-V2 source and its license is included at
`track_a_depth/Depth-Anything-V2/LICENSE` (and the duplicate copy under
`pipeline_georeferenced/track_a_depth/Depth-Anything-V2/LICENSE`). No
modifications were made to the model code itself.

## Sentinel-2 satellite imagery

Sentinel-2 L2A imagery is used to derive the georeferenced pipeline's
elevation estimates, fetched via the
[Earth Search STAC API](https://earth-search.aws.element84.com/v1) (Element
84's public AWS Open Data mirror of Sentinel-2 Cloud-Optimized GeoTIFFs).

Contains modified Copernicus Sentinel data, provided by the European Space
Agency (ESA) under the **Copernicus Programme** of the European Union.
Copernicus Sentinel data is made available under ESA's free, full, and open
data policy; see https://sentinels.copernicus.eu/documents/247904/690755/Sentinel_Data_Legal_Notice
for the current legal notice.

## SRTM elevation data

Reference elevation data (used to calibrate relative depth into absolute
elevation) is the **Shuttle Radar Topography Mission (SRTM)** dataset,
produced by **NASA/JPL and the USGS**, fetched via the no-auth AWS Open Data
"Skadi" mirror (https://s3.amazonaws.com/elevation-tiles-prod/skadi) unless
an OpenTopography API key is supplied. SRTM data produced by U.S. federal
agencies is generally in the public domain within the United States;
redistribution via the mirrors above follows their respective usage terms.

## Three.js

The 3D terrain viewer (`pipeline_georeferenced/3d_visualization/`,
`pipeline_non_georeferenced/frontend/`) is built with
[Three.js](https://threejs.org/), loaded from the unpkg CDN (not vendored in
this repo), licensed under the **MIT License**:

> Copyright © 2010-2025 three.js authors
>
> Permission is hereby granted, free of charge, to any person obtaining a
> copy of this software and associated documentation files (the
> "Software"), to deal in the Software without restriction, including
> without limitation the rights to use, copy, modify, merge, publish,
> distribute, sublicense, and/or sell copies of the Software, and to permit
> persons to whom the Software is furnished to do so, subject to the
> following conditions: the above copyright notice and this permission
> notice shall be included in all copies or substantial portions of the
> Software.

## Leaflet.js

The 2D context map (`pipeline_georeferenced/leaflet_pitch/`) is built with
[Leaflet](https://leafletjs.com/) v1.9.4, loaded from the unpkg CDN (not
vendored in this repo), licensed under the **BSD 2-Clause License**:

> Copyright (c) 2010-2023, Volodymyr Agafonkin
> Copyright (c) 2010-2011, CloudMade
> All rights reserved.
>
> Redistribution and use in source and binary forms, with or without
> modification, are permitted provided that the following conditions are
> met: (1) Redistributions of source code must retain the above copyright
> notice, this list of conditions and the following disclaimer. (2)
> Redistributions in binary form must reproduce the above copyright
> notice, this list of conditions and the following disclaimer in the
> documentation and/or other materials provided with the distribution.
>
> THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS
> IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED
> TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A
> PARTICULAR PURPOSE ARE DISCLAIMED.

## OpenStreetMap basemap tiles

The 2D context map's basemap tiles are © OpenStreetMap contributors,
available under the **Open Database License (ODbL)** — see
https://www.openstreetmap.org/copyright. Attribution is already displayed
live in the map UI itself (`leaflet_pitch/index.html`), as OSM's tile usage
policy requires.
