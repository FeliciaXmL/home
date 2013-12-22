;; http://www.emacswiki.org/emacs/ELPA
(setq package-archives '(("gnu" . "http://elpa.gnu.org/packages/")
                         ("marmalade" . "http://marmalade-repo.org/packages/")
                         ("melpa" . "http://melpa.milkbox.net/packages/")))

;; http://stackoverflow.com/questions/11127109/emacs-24-package-system-initialization-problems
(setq package-enable-at-startup nil)
(package-initialize)

(defvar starter-kit-packages
  (list 'evil
        'auto-complete
        'pos-tip
        'ace-jump-mode
        'expand-region
        'web-mode
        'flycheck
        'go-mode
        'smartparens
        'js2-mode
        'undo-tree ; Automatically loaded by evil.
        'helm)
  "Libraries that should be installed by default.")

(defun starter-kit-elpa-install ()
  "Install all starter-kit packages that aren't installed."
  (interactive)
  (dolist (package starter-kit-packages)
    (unless (or (member package package-activated-list)
                (functionp package))
      (message "Installing %s" (symbol-name package))
      (package-install package))))


;; On your first run, this should pull in all the base packages.
(when (not package-archive-contents) (package-refresh-contents))
(starter-kit-elpa-install)

(server-start)

;; http://ergoemacs.org/emacs/emacs_make_modern.html
(column-number-mode 1)
(defvar backup-dir (expand-file-name "~/.emacs.d/backup/"))
(setq backup-directory-alist (list (cons ".*" backup-dir)))
(setq auto-save-default nil) ; stop creating those #autosave# files

;; Get middle click to work.
;; http://www.emacswiki.org/emacs/CopyAndPaste
(setq x-select-enable-primary t)
(setq select-active-regions t) ;  active region sets primary X11 selection
(global-set-key [mouse-2] 'mouse-yank-primary)


;; http://www.emacswiki.org/emacs/TabBarMode
(tabbar-mode t)
(global-set-key [C-prior] 'tabbar-backward)
(global-set-key [C-next] 'tabbar-forward)
(defun my-tabbar-buffer-groups () ;; customize to show all normal files in one group
   "Returns the name of the tab group names the current buffer belongs to.
 There are two groups: Emacs buffers (those whose name starts with '*', plus
 dired buffers), and the rest.  This works at least with Emacs v24.2 using
 tabbar.el v1.7."
   (list (cond ((string-equal "*" (substring (buffer-name) 0 1)) "emacs")
               ((eq major-mode 'dired-mode) "emacs")
               (t "user"))))
 (setq tabbar-buffer-groups-function 'my-tabbar-buffer-groups)


;; http://www.emacswiki.org/emacs/UndoTree
(undo-tree-mode t)

;; http://www.emacswiki.org/emacs/Evil
(require 'evil)
(setq evil-default-cursor t)
(evil-mode 1)

;; org mode
;; http://orgmode.org/worg/org-tutorials/orgtutorial_dto.html
;; http://orgmode.org/manual/Activation.html#Activation
(global-set-key "\C-cl" 'org-store-link)
(global-set-key "\C-cc" 'org-capture)
(global-set-key "\C-ca" 'org-agenda)
(global-set-key "\C-cb" 'org-iswitchb)

;; tabs/spaces
(setq c-basic-indent 2)
(setq tab-width 4)
(setq indent-tabs-mode nil)

;; http://orgmode.org/manual/Clocking-work-time.html
(org-clock-persistence-insinuate)

;;;;;;;;;;;; AUTOCOMPLETION
; Enables tooltip help
(require 'pos-tip)

; setup autocompletion
(require 'auto-complete-config)
(setq-default ac-sources '(ac-source-dictionary ac-source-words-in-same-mode-buffers ac-source-filename))
(add-hook 'emacs-lisp-mode-hook 'ac-emacs-lisp-mode-setup)
(add-hook 'c-mode-common-hook 'ac-cc-mode-setup)
(add-hook 'css-mode-hook 'ac-css-mode-setup)
(setq ac-auto-start 0)
(setq ac-auto-show-menu t)
(setq ac-quick-help-delay 0.5)
(setq ac-candidate-limit 100)
(add-to-list 'ac-modes 'html-mode)
(add-to-list 'ac-modes 'web-mode)
(setq ac-disable-faces nil)
(global-auto-complete-mode t)

; Enter automatically indents
(define-key global-map (kbd "RET") 'newline-and-indent)

; Don't kill the windows on :bd
(evil-ex-define-cmd "bd[elete]" 'kill-this-buffer)

(require 'expand-region)
(define-key evil-normal-state-map (kbd "e") 'er/expand-region)
(define-key evil-visual-state-map (kbd "e") 'er/expand-region)
(define-key evil-normal-state-map (kbd "E") 'er/contract-region)
(define-key evil-visual-state-map (kbd "E") 'er/contract-region)

(require 'ace-jump-mode)
(define-key evil-normal-state-map (kbd "f") 'ace-jump-mode)

;; Don't wait for any other keys after escape is pressed.
(setq evil-esc-delay 0)

(add-hook 'emacs-lisp-mode-hook (lambda ()
                            (define-key evil-normal-state-map (kbd ".") 'eval-last-sexp)))

(require 'helm-config)
(require 'helm)
(require 'helm-buffers)
(require 'helm-files)

(setq helm-mp-matching-method 'multi3p)
(setq helm-mp-highlight-delay 0.1)
(setq helm-M-x-always-save-history t)
(define-key evil-normal-state-map (kbd "t") 'helm-M-x)
(define-key evil-visual-state-map (kbd "t") 'helm-M-x)

(define-key helm-map (kbd "<escape>") 'helm-keyboard-quit)

(require 'recentf)
(setq recentf-exclude '("\\.recentf" "^/tmp/" "/.git/" "/.emacs.d/elpa/"))
(setq recentf-max-saved-items 100)
(setq recentf-auto-cleanup 'never)
(setq recentf-save-file (expand-file-name "~/.emacs.d/.recentf" user-emacs-directory))
(setq recentf-auto-save-timer
      (run-with-idle-timer 30 t 'recentf-save-list))
(recentf-mode 1)

(defvar helm-source-myrecent
  `((name . "Recentf")
    (candidates . (lambda () recentf-list))
    (no-delay-on-input)
    (action . (("Find file" . find-file)))))
(defun my-helm ()
  (interactive)
  (helm-other-buffer
   '(
     helm-source-myrecent
     helm-c-source-buffers-list)
   " *my-helm*"))

(define-key evil-normal-state-map (kbd ",") 'my-helm)

(require 'flycheck)
(setq flycheck-checkers (delq 'html-tidy flycheck-checkers))
(global-flycheck-mode)

(require 'web-mode)
(add-to-list 'auto-mode-alist '("\\.html?\\'" . web-mode))
(add-to-list 'auto-mode-alist '("\\.js\\'" . js2-mode))

(require 'smartparens-config)
(show-smartparens-global-mode nil)
(smartparens-global-mode t)

; Indentation
(setq-default indent-tabs-mode nil)
(setq-default tab-width 2)
(setq c-basic-offset 2)
(setq css-indent-offset 2)
(setq web-mode-markup-indent-offset 2)
(setq web-mode-css-indent-offset 2)
(setq web-mode-code-indent-offset 2)
(setq-default evil-shift-width 2)

; (global-hl-line-mode 0)
; Save minibuffer history.
(savehist-mode 1)

(require 'saveplace)
(setq-default save-place t)

; Refresh all buffers periodically.
(setq revert-without-query '(".*"))
(global-auto-revert-mode t)

; Persistent undo!
(setq undo-tree-auto-save-history t)
(setq undo-tree-history-directory-alist '((".*" . "~/.emacs.d/undo")))

; Tail the compilation buffer.
(setq compilation-scroll-output t)

(setq mouse-wheel-scroll-amount '(1 ((shift) . 1) ((control) . nil)))
(setq mouse-wheel-progressive-speed t)

;; http://orgmode.org/guide/Publishing.html
(require 'org-publish)
(setq org-publish-project-alist
      '(
        ("org-notes"
         :base-directory "~/info/"
         :base-extension "org"
         :publishing-directory "~/public_html/"
         :recursive t
         :publishing-function org-html-publish-to-html
         :headline-levels 4       ; Just the default for this project.
         :auto-preamble t
         )
        ("org-static"
         :base-directory "~/info/"
         :base-extension "css\\|js\\|png\\|jpg\\|gif\\|pdf\\|mp3\\|ogg\\|swf"
         :publishing-directory "~/public_html/"
         :recursive t
         :publishing-function org-publish-attachment)
        ("org" :components ("org-notes" "org-static"))))

(custom-set-variables
 ;; custom-set-variables was added by Custom.
 ;; If you edit it by hand, you could mess it up, so be careful.
 ;; Your init file should contain only one such instance.
 ;; If there is more than one, they won't work right.
 '(custom-enabled-themes (quote (tango-dark)))
 '(org-agenda-custom-commands (quote (("n" "Agenda and all TODO's" ((agenda "" nil) (alltodo "" nil)) nil) ("r" "Russ Agenda" agenda "" ((org-agenda-overriding-header "Russ Agenda") (org-agenda-view-columns-initially t) (org-agenda-overriding-columns-format "%80ITEM %TAGS %7TODO %5Effort{:} %6CLOCKSUM{Total}"))) ("q" "Russ Todos" alltodo "" ((org-agenda-view-columns-initially t) (org-agenda-overriding-columns-format "%80ITEM %TAGS %7TODO %20SCHEDULED %5Effort{:} %6CLOCKSUM{Total}"))))))
 '(org-agenda-files (quote ("~/info/g.org" "~/info/russ.org")))
 '(org-agenda-skip-deadline-if-done t)
 '(org-agenda-start-on-weekday nil)
 '(org-capture-templates (quote (("t" "Todo" entry (file+headline "~/info/russ.org" "Tasks") "* TODO %^{Brief Description} %^g
%?
Added: %U"))))
 '(org-clock-into-drawer "LOGBOOK")
 '(org-clock-persist t)
 '(org-directory "~/info")
 '(org-global-properties (quote (("Effort_ALL" . "0 0:10 0:30 1:00 2:00 3:00 4:00 8:00 16:00 24:00 40:00"))))
 '(org-hide-leading-stars t)
 '(org-log-into-drawer t)
 '(org-log-note-clock-out t)
 '(org-log-states-order-reversed nil)
 '(org-odd-levels-only t)
 '(org-return-follows-link t)
 '(org-special-ctrl-a/e (quote (t . reversed)))
 '(org-special-ctrl-k t)
 '(org-time-clocksum-use-fractional t)
 '(org-todo-keywords (quote ((sequence "TODO(t)" "WAITING(w@/@)" "DEFERRED(r)" "|" "DONE(d@/@)" "NVM(n@/@)")))))
(custom-set-faces
 ;; custom-set-faces was added by Custom.
 ;; If you edit it by hand, you could mess it up, so be careful.
 ;; Your init file should contain only one such instance.
 ;; If there is more than one, they won't work right.
 )

;;; .emacs ends here
