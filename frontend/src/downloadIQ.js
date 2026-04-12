export default async function downloadIQ(centerFreq) {
  try {
    const res = await fetch(`/api/signals/${centerFreq}/iq`);
    const blob = await res.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `iq_${centerFreq}.bin`;
    a.click();
    window.URL.revokeObjectURL(url);
  } catch (err) {
    console.error('Failed to export I/Q', err);
  }
}
