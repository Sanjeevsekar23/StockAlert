pipeline {
    agent any

    options {
        timestamps()
        skipDefaultCheckout(true)
    }

    environment {
        // Jenkins agents should have Python 3.9+ available on PATH.
        PYTHON = 'python'
    }

    stages {
        stage('Checkout') {
            steps {
                // The Pipeline job's SCM settings provide the repository and branch.
                checkout scm
            }
        }

        stage('Verify Python') {
            steps {
                bat '''
@echo off
where "%PYTHON%"
"%PYTHON%" --version
"%PYTHON%" -c "import sys; assert sys.version_info >= (3, 9), 'Python 3.9 or newer is required'; print(sys.version)"
'''
            }
        }

        stage('Install dependencies') {
            steps {
                bat '"%PYTHON%" -m pip install -r requirements-dev.txt'
            }
        }

        stage('Run tests') {
            steps {
                script {
                    // Keep any app startup database used by the tests outside the real project DB.
                    def testDatabase = "${pwd(tmp: true)}\\stockalert-ci.db"
                    withEnv(["STOCKALERT_DATABASE=${testDatabase}"]) {
                        bat '"%PYTHON%" -m pytest -q --junitxml=junit-test-results.xml'
                    }
                }
            }
        }
    }

    post {
        always {
            junit testResults: 'junit-test-results.xml', allowEmptyResults: true
        }
    }
}
