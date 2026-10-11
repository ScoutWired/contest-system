document.addEventListener('click',e=>{
  const b=e.target.closest('[data-copy]');if(b){navigator.clipboard.writeText(b.getAttribute('data-copy')).then(()=>{b.textContent='Copied!';setTimeout(()=>b.textContent='Copy',1200);});}
  const c=e.target.closest('[data-confirm]');if(c){if(!confirm(c.getAttribute('data-confirm'))){e.preventDefault();}}
});
