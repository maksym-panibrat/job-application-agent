import { Link } from 'react-router-dom'
import { Profile } from '../../api/client'

export function ProfileSummary({ profile }: { profile: Profile }) {
  return (
    <section className="mb-6">
      <h2 className="text-xs uppercase tracking-wider font-bold text-muted mb-2">Profile</h2>
      <div className="bg-surface border border-border rounded-lg-token p-4 space-y-2 text-sm">
        {profile.target_roles.length > 0 && (
          <p><span className="text-muted">Roles: </span>{profile.target_roles.join(', ')}</p>
        )}
        {(profile.target_locations.length > 0 || profile.remote_ok) && (
          <p>
            <span className="text-muted">Locations: </span>
            {[...profile.target_locations, profile.remote_ok ? 'Remote' : null]
              .filter(Boolean).join(', ')}
          </p>
        )}
        {profile.seniority && (
          <p><span className="text-muted">Seniority: </span>{profile.seniority}</p>
        )}
        {profile.search_keywords.length > 0 && (
          <p><span className="text-muted">Keywords: </span>{profile.search_keywords.join(', ')}</p>
        )}
        <p>
          <span className="text-muted">{profile.skills.length} skill{profile.skills.length === 1 ? '' : 's'}</span>
          {' · '}
          <span className="text-muted">{profile.work_experiences.length} experience{profile.work_experiences.length === 1 ? '' : 's'}</span>
        </p>
        <p className="pt-2 border-t border-border text-xs text-muted">
          To change anything here:{' '}
          <Link
            to="?coach=1&prompt=change_profile"
            className="text-accent font-semibold"
          >
            ✦ Open Coach
          </Link>
        </p>
      </div>
    </section>
  )
}
