# Vision2Scale — Track D Frontend

React + Vite + Three.js frontend for Track D.

## What this implements

- Photo upload / drag and drop
- Mock Track C backend so frontend development does not wait for API
- POST `/jobs`
- Poll GET `/jobs/{id}`
- Load GET `/jobs/{id}/result`
- Interactive Three.js OBJ viewer
- Measurement + ground-truth comparison
- Percent error calculation
- `accuracy_log.json` download
- Responsive presentation/demo UI

## 1. Install

```bash
npm install
```

## 2. Run with the mock backend

The project defaults to:

```text
VITE_MOCK_MODE=true
```

Run:

```bash
npm run dev
```

Open the Vite URL shown in the terminal.

The mock produces a simple OBJ mesh and a demo measurement so the complete frontend can be tested before Track C is connected.

## 3. Connect Track C

Create `.env` in the project root:

```env
VITE_MOCK_MODE=false
VITE_API_BASE_URL=http://localhost:8000
```

Then restart Vite:

```bash
npm run dev
```

The frontend expects Track C to expose:

```text
POST /jobs
GET /jobs/{id}
GET /jobs/{id}/result
```

The shared contract defines the job status fields as:

```json
{
  "id": "job_0001",
  "status": "queued",
  "progress": 0,
  "result_url": null,
  "error": null
}
```

### Important integration note

The exact response returned by your friend's `/jobs/{id}/result` endpoint may differ from the example. If it returns a file instead of OBJ text, adjust `getRealResult()` in `src/App.jsx` to load that file.

If the API is on a different origin, Track C must allow browser CORS requests from the Vite development server.

## 4. Accuracy workflow

For each real hero image:

1. Run the image through the live pipeline.
2. Read the measurement produced by Track C.
3. Physically measure the same object.
4. Enter the real measurement in **GROUND TRUTH**.
5. Click **ADD TO ACCURACY LOG**.
6. Download `accuracy_log.json`.

Do not use placeholder ground-truth values in the final report.

## 5. Build

```bash
npm run build
```

The production output will be created in `dist/`.

## Folder

```text
track_d_frontend/
├── index.html
├── package.json
├── .env.example
├── README.md
└── src/
    ├── App.jsx
    ├── main.jsx
    └── styles.css
```
