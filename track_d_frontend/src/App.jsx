import React, { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { OBJLoader } from "three/examples/jsm/loaders/OBJLoader.js";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";
const MOCK_MODE = (import.meta.env.VITE_MOCK_MODE ?? "true") !== "false";

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function makeMockOBJ() {
  return `
o Vision2ScaleMock
v -1 -0.6 -0.6
v  1 -0.6 -0.6
v  1  0.6 -0.6
v -1  0.6 -0.6
v -0.75 -0.45 0.9
v  0.75 -0.45 0.9
v  0.75  0.45 0.9
v -0.75  0.45 0.9
v 0 -0.1 1.5
f 1 2 3 4
f 1 5 6 2
f 2 6 7 3
f 3 7 8 4
f 4 8 5 1
f 5 9 6
f 6 9 7
f 7 9 8
f 8 9 5
`;
}

function percentError(measured, groundTruth) {
  if (!Number.isFinite(measured) || !Number.isFinite(groundTruth) || groundTruth === 0) return null;
  return Math.abs(measured - groundTruth) / Math.abs(groundTruth) * 100;
}

function Viewer({ objText, scale = 1 }) {
  const mountRef = useRef(null);

  useEffect(() => {
    if (!mountRef.current) return;

    const mount = mountRef.current;
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0a0e13);

    const camera = new THREE.PerspectiveCamera(
      45,
      mount.clientWidth / Math.max(mount.clientHeight, 1),
      0.01,
      1000
    );
    camera.position.set(2.8, 2.1, 3.6);

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(mount.clientWidth, mount.clientHeight);
    mount.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.06;
    controls.minDistance = 0.4;
    controls.maxDistance = 30;

    scene.add(new THREE.HemisphereLight(0xffffff, 0x202832, 2.0));
    const key = new THREE.DirectionalLight(0xffffff, 2.5);
    key.position.set(4, 6, 4);
    scene.add(key);

    const grid = new THREE.GridHelper(10, 20, 0x33404d, 0x1b242d);
    grid.position.y = -1.05;
    scene.add(grid);

    const axes = new THREE.AxesHelper(1.5);
    scene.add(axes);

    let object = null;

    if (objText) {
      const loader = new OBJLoader();
      try {
        object = loader.parse(objText);
      } catch (e) {
        console.error("OBJ parse error:", e);
      }
    }

    if (!object) {
      object = new THREE.Mesh(
        new THREE.BoxGeometry(1.8, 1.2, 1.2),
        new THREE.MeshStandardMaterial({ color: 0x65d6ff, roughness: 0.55, metalness: 0.15 })
      );
    }

    object.traverse((child) => {
      if (child.isMesh) {
        child.material = new THREE.MeshStandardMaterial({
          color: 0x65d6ff,
          roughness: 0.48,
          metalness: 0.12,
          side: THREE.DoubleSide
        });
      }
    });

    object.scale.setScalar(Number(scale) || 1);
    scene.add(object);

    const box = new THREE.Box3().setFromObject(object);
    const center = box.getCenter(new THREE.Vector3());
    const size = box.getSize(new THREE.Vector3());
    object.position.sub(center);

    const maxDim = Math.max(size.x, size.y, size.z, 0.1);
    const distance = maxDim * 2.4;
    camera.position.set(distance * 0.9, distance * 0.7, distance * 1.15);
    camera.lookAt(0, 0, 0);
    controls.target.set(0, 0, 0);

    const resize = () => {
      const w = mount.clientWidth;
      const h = Math.max(mount.clientHeight, 1);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
    };

    const observer = new ResizeObserver(resize);
    observer.observe(mount);

    let frame;
    const animate = () => {
      controls.update();
      renderer.render(scene, camera);
      frame = requestAnimationFrame(animate);
    };
    animate();

    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      controls.dispose();
      renderer.dispose();
      if (mount.contains(renderer.domElement)) mount.removeChild(renderer.domElement);
    };
  }, [objText, scale]);

  return <div className="viewer-canvas" ref={mountRef} />;
}

async function mockSubmit(file) {
  await sleep(500);
  return { id: "job_demo_0001", status: "queued", progress: 0, result_url: null, error: null };
}

async function mockStatus(progress) {
  await sleep(700);
  if (progress < 35) return { id: "job_demo_0001", status: "running", progress: 35, result_url: null, error: null };
  if (progress < 70) return { id: "job_demo_0001", status: "running", progress: 70, result_url: null, error: null };
  return { id: "job_demo_0001", status: "done", progress: 100, result_url: "mock://mesh.obj", error: null };
}

async function pollRealJob(jobId, onStatus) {
  for (;;) {
    const res = await fetch(`${API_BASE_URL}/jobs/${jobId}`);
    if (!res.ok) throw new Error(`Status request failed: HTTP ${res.status}`);
    const status = await res.json();
    onStatus(status);
    if (status.status === "done") return status;
    if (status.status === "failed") throw new Error(status.error || "Backend job failed.");
    await sleep(1000);
  }
}

async function getRealResult(jobId, status) {
  const resultUrl = status?.result_url
    ? (status.result_url.startsWith("http") ? status.result_url : `${API_BASE_URL}${status.result_url}`)
    : `${API_BASE_URL}/jobs/${jobId}/result`;

  const res = await fetch(resultUrl);
  if (!res.ok) throw new Error(`Result request failed: HTTP ${res.status}`);

  const contentType = res.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    const data = await res.json();
    return {
      objText: data.obj || data.mesh || data.content || "",
      measured: Number(data.measured ?? data.height ?? data.dimension ?? 0),
      units: data.units || "meters",
      scaleFactor: Number(data.scale_factor ?? data.scaleFactor ?? 1)
    };
  }

  return {
    objText: await res.text(),
    measured: 0,
    units: "meters",
    scaleFactor: 1
  };
}

function App() {
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState("");
  const [status, setStatus] = useState({ status: "idle", progress: 0 });
  const [jobId, setJobId] = useState("");
  const [objText, setObjText] = useState("");
  const [measured, setMeasured] = useState(0);
  const [groundTruth, setGroundTruth] = useState("");
  const [units, setUnits] = useState("meters");
  const [scaleFactor, setScaleFactor] = useState(1);
  const [error, setError] = useState("");
  const [history, setHistory] = useState([]);
  const [dragging, setDragging] = useState(false);

  const errorPct = useMemo(
    () => percentError(Number(measured), Number(groundTruth)),
    [measured, groundTruth]
  );

  const selectFile = (selected) => {
    if (!selected) return;
    if (!selected.type.startsWith("image/")) {
      setError("Please choose a JPG or PNG image.");
      return;
    }
    setError("");
    setFile(selected);
    setPreview(URL.createObjectURL(selected));
    setStatus({ status: "ready", progress: 0 });
    setObjText("");
    setMeasured(0);
    setGroundTruth("");
  };

  const runPipeline = async () => {
    if (!file) {
      setError("Upload an image first.");
      return;
    }

    setError("");
    setObjText("");
    setStatus({ status: "uploading", progress: 5 });

    try {
      let currentStatus;

      if (MOCK_MODE) {
        currentStatus = await mockSubmit(file);
        setJobId(currentStatus.id);
        setStatus(currentStatus);

        currentStatus = await mockStatus(0);
        setStatus(currentStatus);
        currentStatus = await mockStatus(currentStatus.progress);
        setStatus(currentStatus);
        currentStatus = await mockStatus(currentStatus.progress);
        setStatus(currentStatus);

        setObjText(makeMockOBJ());
        setMeasured(1.82);
        setScaleFactor(0.5);
        setUnits("meters");
      } else {
        const formData = new FormData();
        formData.append("photo", file);

        const response = await fetch(`${API_BASE_URL}/jobs`, {
          method: "POST",
          body: formData
        });

        if (!response.ok) throw new Error(`Upload failed: HTTP ${response.status}`);

        const created = await response.json();
        setJobId(created.id);
        setStatus(created);

        currentStatus = await pollRealJob(created.id, setStatus);
        const result = await getRealResult(created.id, currentStatus);

        setObjText(result.objText);
        setMeasured(result.measured);
        setUnits(result.units);
        setScaleFactor(result.scaleFactor);
      }
    } catch (e) {
      console.error(e);
      setStatus({ status: "failed", progress: 0 });
      setError(e.message || "Something went wrong.");
    }
  };

  const saveAccuracy = () => {
    const gt = Number(groundTruth);
    const pred = Number(measured);
    const pct = percentError(pred, gt);

    if (!Number.isFinite(gt) || gt <= 0) {
      setError("Enter the real measured ground-truth dimension first.");
      return;
    }
    if (!Number.isFinite(pred) || pred <= 0) {
      setError("The pipeline has not produced a valid measurement yet.");
      return;
    }

    const row = {
      image_id: file?.name || jobId || `test_${history.length + 1}`,
      measured: pred,
      ground_truth: gt,
      pct_error: Number(pct.toFixed(2))
    };

    const next = [...history, row];
    setHistory(next);
    localStorage.setItem("vision2scale_accuracy_log", JSON.stringify(next, null, 2));
    setError("");
  };

  const downloadAccuracyLog = () => {
    const data = JSON.stringify(history, null, 2);
    const blob = new Blob([data], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "accuracy_log.json";
    a.click();
    URL.revokeObjectURL(url);
  };

  useEffect(() => {
    const saved = localStorage.getItem("vision2scale_accuracy_log");
    if (saved) {
      try { setHistory(JSON.parse(saved)); } catch {}
    }
  }, []);

  return (
    <div className="app-shell">
      <header className="topbar">
        <div>
          <div className="eyebrow">TRACK D · FRONTEND / VALIDATION / DEMO</div>
          <h1>Vision<span>2</span>Scale</h1>
        </div>
        <div className={`mode-pill ${MOCK_MODE ? "mock" : "live"}`}>
          <span className="dot" />
          {MOCK_MODE ? "MOCK BACKEND" : "LIVE API"}
        </div>
      </header>

      <main>
        <section className="hero">
          <div>
            <p className="kicker">SINGLE PHOTO → MEASURABLE 3D</p>
            <h2>Turn one ordinary image into an interactive, scaled 3D scene.</h2>
            <p className="hero-copy">
              Upload a photo, run the pipeline, inspect the reconstructed mesh,
              and compare the predicted dimension against a real measurement.
            </p>
          </div>
          <div className="hero-stat">
            <strong>3D</strong>
            <span>VIEWER</span>
          </div>
        </section>

        <section className="workspace">
          <aside className="control-panel">
            <div className="panel-heading">
              <span>01</span>
              <h3>Input image</h3>
            </div>

            <div
              className={`dropzone ${dragging ? "dragging" : ""} ${file ? "has-file" : ""}`}
              onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragging(false);
                selectFile(e.dataTransfer.files?.[0]);
              }}
              onClick={() => document.getElementById("file-input").click()}
            >
              {preview ? (
                <>
                  <img src={preview} alt="Selected input" />
                  <div className="image-overlay">
                    <span>CHANGE IMAGE</span>
                  </div>
                </>
              ) : (
                <>
                  <div className="upload-icon">↥</div>
                  <strong>Drop image here</strong>
                  <span>or click to browse · JPG / PNG</span>
                </>
              )}
            </div>

            <input
              id="file-input"
              type="file"
              accept="image/jpeg,image/png"
              hidden
              onChange={(e) => selectFile(e.target.files?.[0])}
            />

            {file && <div className="file-row"><span>{file.name}</span><span>{(file.size / 1024 / 1024).toFixed(2)} MB</span></div>}

            <button className="primary-btn" onClick={runPipeline} disabled={!file || ["uploading","queued","running"].includes(status.status)}>
              {["uploading","queued","running"].includes(status.status) ? "PROCESSING…" : "GENERATE 3D MODEL"}
              <span>→</span>
            </button>

            <div className="panel-heading second">
              <span>02</span>
              <h3>Pipeline status</h3>
            </div>

            <div className="status-card">
              <div className="status-line">
                <span className={`status-dot ${status.status}`} />
                <strong>{status.status?.toUpperCase() || "IDLE"}</strong>
                <span>{status.progress || 0}%</span>
              </div>
              <div className="progress-track">
                <div style={{ width: `${status.progress || 0}%` }} />
              </div>
              {jobId && <small>JOB · {jobId}</small>}
            </div>

            {error && <div className="error-box">{error}</div>}
          </aside>

          <section className="viewer-panel">
            <div className="viewer-header">
              <div>
                <div className="eyebrow">03 · RECONSTRUCTION</div>
                <h3>Interactive 3D viewer</h3>
              </div>
              <div className="viewer-help">DRAG TO ORBIT · SCROLL TO ZOOM</div>
            </div>

            <div className="viewer-wrap">
              <Viewer objText={objText} scale={1} />
              {!objText && (
                <div className="viewer-empty">
                  <div className="crosshair">+</div>
                  <strong>3D result will appear here</strong>
                  <span>Start with an image on the left.</span>
                </div>
              )}
              {objText && <div className="viewer-badge">MESH READY</div>}
            </div>

            <div className="measurement-grid">
              <div className="metric">
                <span>PIPELINE MEASUREMENT</span>
                <strong>{measured ? `${measured.toFixed(2)} ${units === "meters" ? "m" : units}` : "—"}</strong>
              </div>
              <div className="metric">
                <span>GROUND TRUTH</span>
                <div className="truth-input">
                  <input
                    type="number"
                    min="0"
                    step="0.01"
                    value={groundTruth}
                    onChange={(e) => setGroundTruth(e.target.value)}
                    placeholder="e.g. 1.90"
                  />
                  <span>m</span>
                </div>
              </div>
              <div className="metric">
                <span>PERCENT ERROR</span>
                <strong>{errorPct == null ? "—" : `${errorPct.toFixed(2)}%`}</strong>
              </div>
              <div className="metric">
                <span>SCALE FACTOR</span>
                <strong>{scaleFactor ? scaleFactor.toFixed(4) : "—"}</strong>
              </div>
            </div>

            <button className="secondary-btn" onClick={saveAccuracy} disabled={!measured}>
              + ADD TO ACCURACY LOG
            </button>
          </section>
        </section>

        <section className="validation">
          <div className="section-title">
            <div>
              <div className="eyebrow">04 · VALIDATION</div>
              <h3>Accuracy log</h3>
            </div>
            <button className="text-btn" onClick={downloadAccuracyLog} disabled={!history.length}>
              DOWNLOAD accuracy_log.json ↗
            </button>
          </div>

          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>IMAGE ID</th>
                  <th>MEASURED</th>
                  <th>GROUND TRUTH</th>
                  <th>PERCENT ERROR</th>
                </tr>
              </thead>
              <tbody>
                {history.length === 0 ? (
                  <tr><td colSpan="4" className="empty-row">No validation runs recorded yet.</td></tr>
                ) : history.map((row, i) => (
                  <tr key={`${row.image_id}-${i}`}>
                    <td>{row.image_id}</td>
                    <td>{row.measured.toFixed(2)} m</td>
                    <td>{row.ground_truth.toFixed(2)} m</td>
                    <td>{row.pct_error.toFixed(2)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </main>

      <footer>
        <span>VISION2SCALE</span>
        <span>TRACK D MVP · FRONTEND / VALIDATION / DEMO</span>
      </footer>
    </div>
  );
}

export default App;