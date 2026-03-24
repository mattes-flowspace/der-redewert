/**
 * Shared contact section for der-redewert.
 * Place <div id="contact-placeholder"></div> in any page.
 * This script replaces it with the contact form HTML.
 *
 * Submissions route to the 'kontakt' form registered on index.html (Netlify Forms).
 */
(function () {
  const el = document.getElementById('contact-placeholder');
  if (!el) return;

  el.outerHTML = `
<section class="contact-section" id="kontakt">
  <div class="contact-info">
    <div class="section-line"></div>
    <h2 class="contact-heading">Kontakt</h2>
    <p>Ich freue mich auf deinen Kontakt! Melde dich gerne über das nebenstehende Kontaktformular oder direkt über <a href="mailto:hallo@der-redewert.com">hallo@der-redewert.com</a></p>
    <p style="margin-top:16px;">10243 Berlin</p>
  </div>
  <form class="contact-form" name="kontakt" action="/" method="POST">
    <input type="hidden" name="form-name" value="kontakt">
    <input type="text"  name="name"     placeholder="Namen eingeben">
    <input type="email" name="email"    placeholder="E-Mail-Adresse eingeben *" required>
    <input type="text"  name="betreff"  placeholder="Betreff eingeben">
    <textarea           name="nachricht" placeholder="Nachricht"></textarea>
    <button type="submit">Absenden</button>
  </form>
</section>`;
})();
