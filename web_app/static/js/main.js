class RosdepAuditorDistributionApp {
    constructor() {
        this.defaultVersionValue = '__default__';
        this.repositories = [];
        this.bindEvents();
        this.loadRepositories();
    }

    bindEvents() {
        document.getElementById('detection-form').addEventListener('submit', (event) => {
            event.preventDefault();
            this.detectDistribution();
        });

        document.getElementById('repo-name').addEventListener('change', () => {
            this.updateRepositoryVersions();
        });

        document.querySelectorAll('.quick-example').forEach((button) => {
            button.addEventListener('click', () => {
                document.getElementById('package-name').value = button.dataset.package;
                document.getElementById('repo-name').value = button.dataset.repo;
                this.updateRepositoryVersions();
                document.getElementById('repo-version').value = button.dataset.version || this.defaultVersionValue;
            });
        });
    }

    async loadRepositories() {
        try {
            const response = await fetch('/api/repositories');
            const data = await response.json();

            if (!response.ok || !data.success) {
                throw new Error(data.error || `HTTP ${response.status}`);
            }

            this.repositories = data.repositories || [];
            this.populateRepositorySelect();
            this.updateStatus('Ready', 'success');
        } catch (error) {
            this.updateStatus('Initialization error', 'danger');
            this.showError(`Failed to load repository configuration: ${error.message}`);
            this.populateRepositorySelect([]);
        }
    }

    populateRepositorySelect() {
        const select = document.getElementById('repo-name');
        select.innerHTML = '<option value="">Select repository...</option>';

        this.repositories.forEach((repo) => {
            const option = document.createElement('option');
            option.value = repo.name;
            option.textContent = repo.name;
            select.appendChild(option);
        });
    }

    updateRepositoryVersions() {
        const repoName = document.getElementById('repo-name').value;
        const versionSelect = document.getElementById('repo-version');
        versionSelect.innerHTML = '<option value="">Select version...</option>';

        const repo = this.repositories.find((item) => item.name === repoName);
        if (!repo) {
            versionSelect.disabled = true;
            return;
        }

        (repo.versions || ['']).forEach((version) => {
            const option = document.createElement('option');
            option.value = version || this.defaultVersionValue;
            option.textContent = version || 'default';
            versionSelect.appendChild(option);
        });
        versionSelect.disabled = false;
    }

    async detectDistribution() {
        const payload = this.getPayload();
        const validationError = this.validatePayload(payload);
        if (validationError) {
            this.showError(validationError);
            return;
        }

        this.showLoading(true);
        this.updateStatus('Analyzing...', 'warning');

        try {
            const response = await fetch('/api/detect', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload),
            });
            const data = await response.json();

            if (!response.ok || !data.success) {
                throw new Error(data.error || `HTTP ${response.status}`);
            }

            this.renderResults(data);
            this.updateStatus('Analysis complete', 'success');
        } catch (error) {
            this.updateStatus('Error', 'danger');
            this.showError(error.message);
        } finally {
            this.showLoading(false);
        }
    }

    getPayload() {
        return {
            package_name: document.getElementById('package-name').value.trim(),
            repo_name: document.getElementById('repo-name').value,
            repo_version: this.selectedRepoVersion(),
            method: document.getElementById('detection-method').value,
            options: {
                top_k: Number.parseInt(document.getElementById('top-k').value, 10),
                score_threshold: Number.parseFloat(document.getElementById('score-threshold').value),
                weight: Number.parseFloat(document.getElementById('weight').value),
                scorer: document.getElementById('scorer').value.trim() || 'default',
                show_scores: document.getElementById('show-scores').checked,
                use_name_matches: document.getElementById('use-name-matches').checked,
                use_source_related: document.getElementById('use-source-related').checked,
                use_cache: document.getElementById('use-cache').checked,
            },
        };
    }

    selectedRepoVersion() {
        const value = document.getElementById('repo-version').value;
        return value === this.defaultVersionValue ? '' : value;
    }

    validatePayload(payload) {
        if (!payload.package_name) {
            return 'Please enter a package name.';
        }
        if (!payload.repo_name) {
            return 'Please select a repository.';
        }
        const repo = this.repositories.find((item) => item.name === payload.repo_name);
        const defaultVersionAllowed = repo && (repo.versions || []).includes('');
        if (!payload.repo_version && !defaultVersionAllowed) {
            return 'Please select a repository version.';
        }
        return null;
    }

    renderResults(data) {
        const container = document.getElementById('results-container');
        if (data.standard_distribution) {
            container.innerHTML = this.renderMultiModeResults(data);
            this.bindResultTabs();
            return;
        }
        container.innerHTML = this.renderSingleResult(data.baseline_package, data.distribution_table);
    }

    renderSingleResult(baselinePackage, distributionTable) {
        return `
            ${this.renderStats(distributionTable)}
            ${this.renderBaseline(baselinePackage)}
            ${this.renderRepositories(distributionTable)}
        `;
    }

    renderMultiModeResults(data) {
        const errors = data.errors && data.errors.llm
            ? `<div class="alert alert-warning"><strong>LLM results unavailable:</strong> ${this.escapeHtml(data.errors.llm)}</div>`
            : '';

        return `
            ${errors}
            <div class="multi-mode-tabs" role="tablist">
                <button class="multi-mode-tab active" data-mode="standard" type="button">Standard</button>
                <button class="multi-mode-tab" data-mode="llm" type="button">LLM</button>
                <button class="multi-mode-tab" data-mode="all" type="button">All Candidates</button>
            </div>
            <div id="standard-content" class="mode-content">
                ${this.renderSingleResult(data.baseline_package, data.standard_distribution)}
            </div>
            <div id="llm-content" class="mode-content d-none">
                ${this.renderSingleResult(data.baseline_package, data.llm_distribution)}
            </div>
            <div id="all-content" class="mode-content d-none">
                ${this.renderSingleResult(data.baseline_package, data.all_candidates)}
            </div>
        `;
    }

    bindResultTabs() {
        document.querySelectorAll('.multi-mode-tab').forEach((tab) => {
            tab.addEventListener('click', () => {
                const mode = tab.dataset.mode;
                document.querySelectorAll('.multi-mode-tab').forEach((item) => item.classList.remove('active'));
                document.querySelectorAll('.mode-content').forEach((item) => item.classList.add('d-none'));
                tab.classList.add('active');
                document.getElementById(`${mode}-content`).classList.remove('d-none');
            });
        });
    }

    renderStats(distributionTable) {
        const totalPackages = distributionTable ? distributionTable.total_packages : 0;
        const repositories = distributionTable ? distributionTable.repositories.length : 0;

        return `
            <div class="stats-card">
                <div class="row">
                    <div class="col-md-6">
                        <div class="stats-number">${totalPackages}</div>
                        <div class="stats-label">Packages Found</div>
                    </div>
                    <div class="col-md-6">
                        <div class="stats-number">${repositories}</div>
                        <div class="stats-label">Repositories</div>
                    </div>
                </div>
            </div>
        `;
    }

    renderBaseline(baselinePackage) {
        return `
            <div class="result-card">
                <div class="result-header">
                    <h5 class="mb-0"><i class="fas fa-package me-2"></i>Baseline Package</h5>
                </div>
                <div class="result-body">
                    <div class="package-item mb-0">
                        <div class="package-name">${this.escapeHtml(baselinePackage.name)}</div>
                        <div class="package-description">
                            Repository:
                            <span class="repo-badge">${this.escapeHtml(`${baselinePackage.repo}_${baselinePackage.version}`)}</span>
                        </div>
                    </div>
                </div>
            </div>
        `;
    }

    renderRepositories(distributionTable) {
        const repositories = distributionTable ? distributionTable.repositories : [];
        if (!repositories.length || !distributionTable.total_packages) {
            return `
                <div class="result-card">
                    <div class="result-body text-center text-muted p-5">
                        <i class="fas fa-circle-info fa-2x mb-3"></i>
                        <h5>No equivalent packages found</h5>
                        <p class="mb-0">Try another repository, lower the score threshold, or enable more candidate sources.</p>
                    </div>
                </div>
            `;
        }

        return repositories
            .filter((repo) => repo.packages.length)
            .map((repo) => this.renderRepository(repo))
            .join('');
    }

    renderRepository(repo) {
        const packages = repo.packages.map((pkg) => this.renderPackage(pkg)).join('');
        return `
            <div class="result-card">
                <div class="result-header">
                    <h5 class="mb-0">
                        <i class="fas fa-server me-2"></i>${this.escapeHtml(repo.repo_key)}
                        <span class="badge bg-secondary ms-2">${repo.package_count} package(s)</span>
                    </h5>
                </div>
                <div class="result-body">${packages}</div>
            </div>
        `;
    }

    renderPackage(pkg) {
        const metadata = this.renderMetadata(pkg.metadata || {});
        const files = this.renderFiles(pkg);
        const version = pkg.version ? `<span class="detail-chip">version ${this.escapeHtml(pkg.version)}</span>` : '';
        const source = pkg.src_name ? `<span class="detail-chip">source ${this.escapeHtml(pkg.src_name)}</span>` : '';

        return `
            <div class="package-item">
                <div>
                    <div class="package-name">${this.escapeHtml(pkg.name)}</div>
                    <div class="package-description">${this.escapeHtml(pkg.description)}</div>
                </div>
                <div class="package-details mt-2">
                    ${version}
                    ${source}
                    <span class="detail-chip">${this.escapeHtml(pkg.repo_name)} ${this.escapeHtml(pkg.repo_version || '')}</span>
                </div>
                ${metadata}
                ${files}
            </div>
        `;
    }

    renderMetadata(metadata) {
        const entries = Object.entries(metadata).filter(([, value]) => value !== null && value !== '');
        if (!entries.length) {
            return '';
        }

        const rows = entries.map(([key, value]) => `
            <div><strong>${this.escapeHtml(key)}:</strong> ${this.escapeHtml(this.formatValue(value))}</div>
        `).join('');

        return `<div class="metadata-block mt-2">${rows}</div>`;
    }

    renderFiles(pkg) {
        if (!pkg.filelist || !pkg.filelist.length) {
            return '';
        }

        const files = pkg.filelist.slice(0, 8)
            .map((file) => `<div class="file-item">${this.escapeHtml(file)}</div>`)
            .join('');
        const more = pkg.filelist_count > pkg.filelist.length
            ? `<div class="file-item text-muted">... and ${pkg.filelist_count - pkg.filelist.length} more</div>`
            : '';

        return `
            <div class="mt-3">
                <small class="text-muted">Sample files</small>
                <div class="file-list">${files}${more}</div>
            </div>
        `;
    }

    formatValue(value) {
        if (Array.isArray(value)) {
            return value.join(', ');
        }
        if (value && typeof value === 'object') {
            return JSON.stringify(value);
        }
        return String(value);
    }

    showLoading(show) {
        const spinner = document.getElementById('loading-spinner');
        const button = document.getElementById('detect-btn');
        const results = document.getElementById('results-container');

        spinner.classList.toggle('d-none', !show);
        button.disabled = show;
        button.innerHTML = show
            ? '<i class="fas fa-spinner fa-spin me-2"></i>Analyzing...'
            : '<i class="fas fa-search me-2"></i>Detect Distribution';

        if (show) {
            results.innerHTML = '';
        }
    }

    updateStatus(message, type) {
        document.getElementById('status-indicator').innerHTML =
            `<i class="fas fa-circle text-${type} me-1"></i>${this.escapeHtml(message)}`;
    }

    showError(message) {
        document.getElementById('error-message').textContent = message;
        const modalElement = document.getElementById('error-modal');
        if (window.bootstrap && window.bootstrap.Modal) {
            new window.bootstrap.Modal(modalElement).show();
        } else {
            alert(message);
        }
    }

    escapeHtml(value) {
        const element = document.createElement('div');
        element.textContent = value == null ? '' : String(value);
        return element.innerHTML;
    }
}

document.addEventListener('DOMContentLoaded', () => {
    new RosdepAuditorDistributionApp();
});
