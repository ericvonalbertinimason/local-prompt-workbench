// Applies the saved or system theme before the page paints. A separate file because the
// Content-Security-Policy allows no inline scripts.
try{var t=localStorage.getItem("theme");if(t==="dark"||(!t&&matchMedia("(prefers-color-scheme: dark)").matches))document.documentElement.dataset.theme="dark"}catch(e){}
