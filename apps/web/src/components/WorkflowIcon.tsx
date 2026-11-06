type Workflow = 'enterprise' | 'opportunities' | 'policy' | 'report' | 'trace' | 'admin';

export default function WorkflowIcon({ name }: { name: Workflow }) {
  return <svg className="workflow-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {name === 'enterprise' && <><circle cx="12" cy="8" r="3" /><path d="M5 20v-1a7 7 0 0 1 14 0v1H5Z" /></>}
    {name === 'opportunities' && <><circle cx="10.5" cy="10.5" r="6.5" /><path d="m15.5 15.5 4 4M8 10.5h5M10.5 8v5" /></>}
    {name === 'policy' && <><path d="M6 3h8l4 4v14H6zM14 3v5h4M9 12h6M9 16h6" /></>}
    {name === 'report' && <><path d="M9 5h11M9 12h11M9 19h11M4 5l1 1 2-2M4 12l1 1 2-2M4 19l1 1 2-2" /></>}
    {name === 'trace' && <><circle cx="5" cy="6" r="2" /><circle cx="19" cy="18" r="2" /><path d="M7 6h5a4 4 0 0 1 0 8H9a4 4 0 0 0 0 8h2" /></>}
    {name === 'admin' && <><path d="M12 3 19 6v5c0 4-2.7 7.2-7 10-4.3-2.8-7-6-7-10V6zM9 12l2 2 4-4" /></>}
  </svg>;
}
