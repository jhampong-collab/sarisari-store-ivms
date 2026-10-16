"use strict";
let cfg, token=null;
const $=id=>document.getElementById(id);
let message=t=>{$('message').textContent=t};
const request=async(path,method='GET',body=null,auth=false)=>{
 const r=await fetch(path,{method,headers:{'Content-Type':'application/json',...(auth?{'Authorization':'Bearer '+token,'X-Requested-With':'sari-app'}:{'X-Requested-With':'sari-app'})},body:body?JSON.stringify(body):undefined});
 const j=await r.json().catch(()=>({})); if(!r.ok)throw Error(j.msg||j.error_description||j.error||'Request failed');return j;
};
const supa=async(path,body)=>request(cfg.url+'/auth/v1/'+path,'POST',body);
async function init(){cfg=await request('/api/v2/config');if(!cfg.url||!cfg.key)throw Error('Configure Supabase URL and publishable key on Render.');
 // Use session storage only for this onboarding test; never put passwords in storage.
 token=sessionStorage.getItem('sari_v2_test_token');
 const hash=new URLSearchParams(location.hash.slice(1));if(hash.get('access_token')){token=hash.get('access_token');sessionStorage.setItem('sari_v2_test_token',token);history.replaceState({},'',location.pathname);message('Email confirmed. Click Activate my verified store.');}
}
async function auth(path,body){const r=await fetch(cfg.url+'/auth/v1/'+path+(path==='signup'?'?redirect_to='+encodeURIComponent(location.origin+'/'):''),{method:'POST',headers:{'apikey':cfg.key,'Content-Type':'application/json'},body:JSON.stringify(body)});const j=await r.json().catch(()=>({}));if(!r.ok)throw Error(j.msg||j.error_description||j.error||'Authentication failed');return j;}
$('signup').onclick=async()=>{try{if(!$('store').value.trim()||!$('name').value.trim())throw Error('Enter store name and owner name.');if($('password').value.length<10)throw Error('Use at least 10 password characters.');await auth('signup',{email:$('email').value.trim(),password:$('password').value,data:{store_name:$('store').value.trim(),full_name:$('name').value.trim()},options:{email_redirect_to:location.origin+'/'} });message('Check your email for the verification link. After verification, return here and sign in.');}catch(e){message(e.message)}};
$('signin').onclick=async()=>{try{const j=await auth('token?grant_type=password',{email:$('email').value.trim(),password:$('password').value});if(!j.access_token)throw Error('Sign in failed. Verify your email first.');token=j.access_token;sessionStorage.setItem('sari_v2_test_token',token);message('Signed in. Click Activate my verified store.');}catch(e){message(e.message)}};
$('activate').onclick=async()=>{try{if(!token)throw Error('Please sign in first.');const j=await request('/api/v2/provision','POST',{},true);message(j.message+'\nStore ID: '+j.store_id);}catch(e){message(e.message)}};
$('signout').onclick=()=>{token=null;sessionStorage.removeItem('sari_v2_test_token');message('Signed out locally.')};
init().catch(e=>message(e.message));

// Presentation controls only; existing authentication handlers are preserved.
const showMode=mode=>{const reg=mode==='register';$('register-fields').hidden=!reg;$('signup').hidden=!reg;$('signin').hidden=reg;$('password-hint').hidden=!reg;$('heading').textContent=reg?'Create your store':'Welcome back';$('subtitle').textContent=reg?'Start with a verified, private store account.':'Sign in to your store workspace.';$('tab-login').classList.toggle('active',!reg);$('tab-register').classList.toggle('active',reg);$('password').autocomplete=reg?'new-password':'current-password';};
$('tab-login').onclick=()=>showMode('login');$('tab-register').onclick=()=>showMode('register');$('toggle-password').onclick=()=>{const input=$('password');input.type=input.type==='password'?'text':'password';$('toggle-password').textContent=input.type==='password'?'Show':'Hide';};
const oldMessage=message;message=t=>{oldMessage(t);if(/Signed in|Email confirmed/.test(t)){$('activate').hidden=false;$('signout').hidden=false;}if(/Store account activated/.test(t)){$('activate').hidden=true;$('signout').hidden=false;}if(/Signed out/.test(t)){$('activate').hidden=true;$('signout').hidden=true;}};
showMode('login');
