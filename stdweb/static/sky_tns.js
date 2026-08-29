document.addEventListener('DOMContentLoaded', function () {
  const node = document.getElementById('sky-data');
  const box = document.getElementById('aladin-lite-div');
  if (!node || !box || typeof A === 'undefined') return;
  const sky = JSON.parse(node.textContent);
  A.init.then(function () {
    const aladin = A.aladin('#aladin-lite-div', {
      survey: 'P/DSS2/color',
      fov: 0.12,
      target: sky.ra + ' ' + sky.dec,
      cooFrame: 'ICRSd',
      showFullscreenControl: true,
      showProjectionControl: false,
      showShareControl: false
    });
    const targetCat = A.catalog({ name: 'STDWeb', sourceSize: 22, color: '#7eb6ff', shape: 'circle' });
    aladin.addCatalog(targetCat);
    targetCat.addSources([
      A.marker(sky.ra, sky.dec, {
        popupTitle: sky.name || 'Target',
        popupDesc: 'STDWeb photometry position'
      })
    ]);
    if (sky.tns_ra != null && sky.tns_dec != null) {
      const tnsCat = A.catalog({ name: 'TNS', sourceSize: 16, color: '#e07a5f', shape: 'plus' });
      aladin.addCatalog(tnsCat);
      tnsCat.addSources([
        A.marker(sky.tns_ra, sky.tns_dec, {
          popupTitle: sky.tns_name || 'TNS',
          popupDesc: 'TNS coordinates'
        })
      ]);
    }
  }).catch(function (err) {
    box.textContent = 'Sky view could not load (WebGL may be unavailable).';
    console.error(err);
  });
});
