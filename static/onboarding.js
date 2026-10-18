"use strict";
let cfg, token=null;
const $=id=>document.getElementById(id);
const invitation=new URLSearchParams(location.search).get('invite');
const isStaff=Boolean(invitation && /^[0-9a-f-]{36}$/i.test(invitation));
const invitationRedirect=location.origin+'/?invite='+encodeURIComponent(invitation||'');

let message=t=>{$('message').textContent=t};
const request=async(path,method='GET',body=null,auth=false)=>{
 const r=await fetch(path,{method,headers:{'Content-Type':'application/json',...(auth?{'Authorization':'Bearer '+token,'X-Requested-With':'sari-app'}:{'X-Requested-With':'sari-app'})},body:body?JSON.stringify(body):undefined});
 const j=await r.json().catch(()=>({})); if(!r.ok)throw Error(j.msg||j.error_description||j.error||'Request failed');return j;
};
const supa=async(path,body)=>request(cfg.url+'/auth/v1/'+path,'POST',body);
async function init(){cfg=await request('/api/v2/config');if(!cfg.url||!cfg.key)throw Error('Configure Supabase URL and publishable key on Render.');
 // Use session storage only for this onboarding test; never put passwords in storage.
 token=sessionStorage.getItem('sari_v2_test_token');
 const hash=new URLSearchParams(location.hash.slice(1));if(hash.get('access_token')){token=hash.get('access_token');sessionStorage.setItem('sari_v2_test_token',token);history.replaceState({},'',location.pathname+location.search);message(isStaff?'Email verified. Sign in to join your store.':'Email confirmed. Sign in to activate your store.');}
}
async function auth(path,body){const r=await fetch(cfg.url+'/auth/v1/'+path+(path==='signup'?'?redirect_to='+encodeURIComponent(isStaff?invitationRedirect:location.origin+'/'):''),{method:'POST',headers:{'apikey':cfg.key,'Content-Type':'application/json'},body:JSON.stringify(body)});const j=await r.json().catch(()=>({}));if(!r.ok)throw Error(j.msg||j.error_description||j.error||'Authentication failed');return j;}
$('signup').onclick=async()=>{try{if(!$('store').value.trim()||!$('name').value.trim())throw Error('Enter store name and owner name.');if($('password').value.length<10)throw Error('Use at least 10 password characters.');await auth('signup',{email:$('email').value.trim(),password:$('password').value,data:{store_name:$('store').value.trim(),full_name:$('name').value.trim()},options:{email_redirect_to:location.origin+'/'} });message('Check your email for the verification link. After verification, return here and sign in.');}catch(e){message(e.message)}};
$('signin').onclick=async()=>{try{const j=await auth('token?grant_type=password',{email:$('email').value.trim(),password:$('password').value});if(!j.access_token)throw Error('Sign in failed. Verify your email first.');token=j.access_token;sessionStorage.setItem('sari_v2_test_token',token);message('Signed in. Checking your store…');try{const a=await request('/api/v2/account','GET',null,true);if(isStaff){await acceptStaff();return;}if(a.store){location.assign('/dashboard');return;}}catch(e){}message(isStaff?'Signed in. Accepting your staff invitation…':'Click Activate my verified store.');}catch(e){message(e.message)}};
$('activate').onclick=async()=>{try{if(!token)throw Error('Please sign in first.');const j=await request('/api/v2/provision','POST',{},true);message(j.message+'\nStore ID: '+j.store_id);location.assign('/dashboard');}catch(e){message(e.message)}};
$('signout').onclick=()=>{token=null;sessionStorage.removeItem('sari_v2_test_token');message('Signed out locally.')};
init().catch(e=>message(e.message));

// Presentation controls only; existing authentication handlers are preserved.
const showMode=mode=>{const reg=mode==='register';$('register-fields').hidden=!reg||isStaff;$('signup').hidden=!reg||isStaff;$('staff-signup').hidden=!reg||!isStaff;$('signin').hidden=reg;$('password-hint').hidden=!reg;$('heading').textContent=reg?(isStaff?'Create staff account':'Create your store'):(isStaff?'Staff sign in':'Welcome back');$('subtitle').textContent=reg?(isStaff?'Create your personal login to join the invited store.':'Start with a verified, private store account.'):(isStaff?'Sign in to join your assigned store.':'Sign in to your store workspace.');$('tab-login').classList.toggle('active',!reg);$('tab-register').classList.toggle('active',reg);$('password').autocomplete=reg?'new-password':'current-password';};
$('tab-login').onclick=()=>showMode('login');$('tab-register').onclick=()=>showMode('register');$('toggle-password').onclick=()=>{const input=$('password');input.type=input.type==='password'?'text':'password';$('toggle-password').textContent=input.type==='password'?'Show':'Hide';};
const oldMessage=message;message=t=>{oldMessage(t);if(/Signed in|Email confirmed/.test(t)){$('activate').hidden=isStaff;$('signout').hidden=false;}if(/Store account activated/.test(t)){$('activate').hidden=true;$('signout').hidden=false;}if(/Signed out/.test(t)){$('activate').hidden=true;$('signout').hidden=true;}};
showMode('login');

$('staff-accept').onclick=async()=>{try{if(!token)throw Error('Sign in first with the invited email.');const id=$('staff-invite-id').value.trim();const j=await request('/api/v2/staff/accept','POST',{invitation_id:id},true);message(j.message);location.assign('/dashboard')}catch(e){message(e.message)}};

// Step 7B.5: link-based staff onboarding. Never provision a new store for staff.
async function acceptStaff(){
  if(!isStaff || !token)throw Error('Open the invitation link and sign in first.');
  const result=await request('/api/v2/staff/accept','POST',{invitation_id:invitation},true);
  message(result.message);
  location.assign('/dashboard');
}
$('staff-signup').onclick=async()=>{
  try{
    if(!isStaff)throw Error('A staff invitation link is required.');
    if($('password').value.length<10)throw Error('Use at least 10 password characters.');
    const email=$('email').value.trim();
    if(!email)throw Error('Enter your invited email.');
    await auth('signup',{email,password:$('password').value,
      data:{full_name:email.split('@')[0]},
      options:{email_redirect_to:invitationRedirect}});
    message('Check your email and verify your account. Then return to this invitation link and sign in.');
  }catch(e){message(e.message)}
};
if(isStaff){
  $('staff-accept-box').hidden=true;
  $('activate').hidden=true;
  $('heading').textContent='Staff sign in';
  $('subtitle').textContent='Use your invited email to join your assigned store.';
  $('tab-register').textContent='Create staff account';
  $('tab-login').textContent='Staff sign in';
  $('register-fields').hidden=true;
  const notice=document.querySelector('.notice');
  if(notice)notice.textContent='Staff accounts use Supabase email verification. You will not create a new store.';
  // Do not offer the owner activation flow for invitation links.
  $('activate').hidden=true;
}
