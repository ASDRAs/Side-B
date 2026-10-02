globalThis.SideBEqPresets = (() => {
  const flat = () => ({ preamp: 0, bands: [] });
  // Deliberately a cut-only test preset: audible without adding clipping risk.
  const test = () => ({ preamp: 0, bands: [{ frequency: 1000, gain: -12, q: 0.7 }] });

  // Product-side starting values, not model output or an accuracy claim.
  // The keys are stable API IDs, independent of model label encoder strings.
  // Columns: 80 / 250 / 1000 / 4000 / 10000 Hz, gain in dB.
  const genreGains = {
    pop: [1, -1, 0, 1, 1],
    rnb_soul: [1, 1, 0, -1, 1],
    dance: [2, 0, -1, 1, 1],
    hiphop: [2, 1, 0, -1, 0],
    rock_metal: [1, -1, 1, 1, 0],
    ballad: [-1, 0, 1, 1, 0],
    blues: [1, 2, 0, 1, 0],
    jazz: [0, 1, 0, -1, 1],
    country: [0, 1, -1, 1, 2],
    folk: [-1, 1, 1, 1, 1],
    // Compatibility presets for the previous inference revision.
    jpop: [1, -1, 0, 1, 1],
    folk_blues_country: [0, 1, 0, 1, 1],
  };
  function forGenre(genre) {
    if (!Object.hasOwn(genreGains, genre)) return null;
    return validate({ preamp: 0, bands: genreGains[genre].map((gain, index) => ({
      frequency: [80, 250, 1000, 4000, 10000][index], gain, q: 1,
    })) });
  }

  function validate(preset) {
    if (!preset || !Array.isArray(preset.bands) || preset.bands.length > 16) {
      throw new Error("Invalid EQ bands");
    }
    const preamp = preset.preamp ?? 0;
    if (!Number.isFinite(preamp) || preamp < -30 || preamp > 0) {
      throw new Error("Invalid EQ preamp");
    }
    const frequencies = new Set();
    const bands = preset.bands.map((band) => {
      const { frequency, gain, q = 1.4 } = band || {};
      if (!Number.isFinite(frequency) || frequency < 20 || frequency > 20000 ||
          !Number.isFinite(gain) || gain < -12 || gain > 12 ||
          !Number.isFinite(q) || q < 0.1 || q > 18 || frequencies.has(frequency)) {
        throw new Error("Invalid EQ band");
      }
      frequencies.add(frequency);
      return { frequency, gain, q };
    });
    return { preamp, bands };
  }

  function trackKey(track) {
    if (!track?.title?.trim()) return "";
    return JSON.stringify([track.videoId || "", track.title.trim(), track.artist || ""]);
  }

  function supportedGenres() {
    return Object.keys(genreGains);
  }

  return { flat, test, validate, trackKey, forGenre, supportedGenres };
})();
