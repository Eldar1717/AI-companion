/**
 * Optional in-browser face tracking (MediaPipe Face Landmarker, WASM).
 *
 * Used for: gaze direction, blink detection and subtle head movement.
 * If the bundle/model is unavailable the avatar falls back to its procedural
 * animation - the project never depends on it.
 */

let visionModule = null;

async function loadVision() {
  if (visionModule) return visionModule;
  try {
    visionModule = await import('/vendor/vision_bundle.mjs');
  } catch (err) {
    console.info('MediaPipe bundle не найден:', err.message);
    return null;
  }
  return visionModule;
}

export class FaceTracker {
  constructor(landmarker, vision) {
    this.landmarker = landmarker;
    this.vision = vision;
    this.lastResult = null;
    this.enabled = false;
  }

  static async create(modelUrl, wasmDir = '/vendor/wasm', delegate = 'GPU') {
    const vision = await loadVision();
    if (!vision) return null;
    try {
      const fileset = await vision.FilesetResolver.forVisionTasks(wasmDir);
      const landmarker = await vision.FaceLandmarker.createFromOptions(fileset, {
        baseOptions: { modelAssetPath: modelUrl, delegate },
        runningMode: 'VIDEO',
        numFaces: 1,
        outputFaceBlendshapes: false,
        outputFacialTransformationMatrixes: false,
      });
      return new FaceTracker(landmarker, vision);
    } catch (err) {
      console.info('FaceLandmarker недоступен:', err.message);
      return null;
    }
  }

  /**
   * Track one frame. `toCanvas` maps normalized [0,1] landmarks to canvas px.
   */
  track(canvas, toCanvas, timestamp) {
    if (!this.landmarker || !this.enabled) return null;
    try {
      const image = new this.vision.Image(canvas);
      const result = this.landmarker.detectForVideo(image, timestamp);
      this.lastResult = result;
      if (!result.faceLandmarks || !result.faceLandmarks.length) return null;
      const lm = result.faceLandmarks[0];
      const P = (i) => toCanvas(lm[i].x, lm[i].y);

      const rOuter = P(33), rInner = P(133);
      const lOuter = P(263), lInner = P(362);
      const rUp = P(159), rLo = P(145);
      const lUp = P(386), lLo = P(374);
      const rIris = P(468), lIris = P(473);
      const nose = P(1);
      const chin = P(152), forehead = P(10);

      const rCenter = { x: (rOuter.x + rInner.x) / 2, y: (rOuter.y + rInner.y) / 2 };
      const lCenter = { x: (lOuter.x + lInner.x) / 2, y: (lOuter.y + lInner.y) / 2 };
      const rWidth = Math.max(1, Math.abs(rInner.x - rOuter.x));
      const lWidth = Math.max(1, Math.abs(lOuter.x - lInner.x));

      const irisOffset = (iris, center, halfWidth) => ((iris.x - center.x) / halfWidth);

      const aspect = (up, lo, width) => {
        const dy = Math.hypot(up.x - lo.x, up.y - lo.y);
        return dy / width;
      };

      const faceCenter = { x: (nose.x + chin.x) / 2, y: (forehead.y + chin.y) / 2 };
      const faceWidth = Math.max(1, Math.abs(lOuter.x - rOuter.x));

      return {
        gazeX: (irisOffset(rIris, rCenter, rWidth / 2) + irisOffset(lIris, lCenter, lWidth / 2)) / 2,
        gazeY: ((rIris.y - rCenter.y) / (rWidth / 2) + (lIris.y - lCenter.y) / (lWidth / 2)) / 2,
        blinkLeft: aspect(rUp, rLo, rWidth),
        blinkRight: aspect(lUp, lLo, lWidth),
        yaw: (nose.x - faceCenter.x) / faceWidth,
        pitch: (nose.y - faceCenter.y) / faceWidth,
      };
    } catch (err) {
      console.warn('Сбой слежения:', err.message);
      return null;
    }
  }

  close() {
    try {
      this.landmarker && this.landmarker.close();
    } catch (err) {
      /* ignore */
    }
    this.enabled = false;
  }
}
