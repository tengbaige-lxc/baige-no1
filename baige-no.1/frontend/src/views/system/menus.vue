<template>
  <div class="page-container">
    <el-card shadow="never">
      <div class="toolbar">
        <el-button type="primary" @click="handleAdd">
          <el-icon><Plus /></el-icon> 新增菜单
        </el-button>
      </div>

      <el-table
        :data="menuList"
        v-loading="loading"
        border
        stripe
        row-key="id"
        :tree-props="{ children: 'children', hasChildren: 'hasChildren' }"
        default-expand-all
      >
        <el-table-column prop="title" label="菜单名称" min-width="160" />
        <el-table-column prop="name" label="路由名称" min-width="140" />
        <el-table-column prop="path" label="路由路径" min-width="160" />
        <el-table-column prop="component" label="组件路径" min-width="160" />
        <el-table-column prop="icon" label="图标" width="100">
          <template #default="{ row }">
            <el-icon v-if="row.icon"><component :is="row.icon" /></el-icon>
          </template>
        </el-table-column>
        <el-table-column prop="sort_order" label="排序" width="80" />
        <el-table-column prop="menu_type" label="类型" width="100">
          <template #default="{ row }">
            <el-tag v-if="row.menu_type === 'directory'" type="warning">目录</el-tag>
            <el-tag v-else-if="row.menu_type === 'menu'" type="success">菜单</el-tag>
            <el-tag v-else type="info">按钮</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="is_hidden" label="显示" width="80">
          <template #default="{ row }">
            <el-tag :type="row.is_hidden ? 'info' : 'success'">{{ row.is_hidden ? '隐藏' : '显示' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="180" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="handleAddChild(row)">新增</el-button>
            <el-button link type="primary" @click="handleEdit(row)">编辑</el-button>
            <el-button link type="danger" @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="dialogVisible" :title="dialogTitle" width="550px">
      <el-form :model="form" :rules="formRules" ref="formRef" label-width="90px">
        <el-form-item label="上级菜单">
          <el-tree-select
            v-model="form.parent_id"
            :data="menuTreeOptions"
            :props="{ label: 'title', value: 'id', children: 'children' }"
            clearable
            placeholder="顶级菜单"
            check-strictly
            style="width: 100%;"
          />
        </el-form-item>
        <el-form-item label="菜单类型">
          <el-radio-group v-model="form.menu_type">
            <el-radio-button label="directory">目录</el-radio-button>
            <el-radio-button label="menu">菜单</el-radio-button>
            <el-radio-button label="button">按钮</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="菜单名称" prop="title">
          <el-input v-model="form.title" />
        </el-form-item>
        <el-form-item label="路由名称" prop="name">
          <el-input v-model="form.name" />
        </el-form-item>
        <el-form-item label="路由路径" prop="path">
          <el-input v-model="form.path" />
        </el-form-item>
        <el-form-item label="组件路径" v-if="form.menu_type === 'menu'">
          <el-input v-model="form.component" placeholder="如: views/system/users.vue" />
        </el-form-item>
        <el-form-item label="权限标识" v-if="form.menu_type === 'button'">
          <el-input v-model="form.permission" />
        </el-form-item>
        <el-form-item label="图标">
          <el-input v-model="form.icon" />
        </el-form-item>
        <el-form-item label="排序">
          <el-input-number v-model="form.sort_order" :min="0" />
        </el-form-item>
        <el-form-item label="显示状态">
          <el-radio-group v-model="form.is_hidden">
            <el-radio-button :label="false">显示</el-radio-button>
            <el-radio-button :label="true">隐藏</el-radio-button>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="handleSubmit" :loading="submitLoading">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getMenuList, createMenu, updateMenu, deleteMenu } from '@/api/menu'

const loading = ref(false)
const submitLoading = ref(false)
const dialogVisible = ref(false)
const dialogTitle = ref('')
const isEdit = ref(false)
const formRef = ref(null)
const menuList = ref([])
const menuTreeOptions = ref([{ id: null, title: '顶级菜单', children: [] }])

const form = reactive({
  id: null,
  parent_id: null,
  name: '',
  path: '',
  component: '',
  icon: '',
  title: '',
  sort_order: 0,
  menu_type: 'menu',
  permission: '',
  is_hidden: false,
})

const formRules = {
  title: [{ required: true, message: '请输入菜单名称', trigger: 'blur' }],
  name: [{ required: true, message: '请输入路由名称', trigger: 'blur' }],
  path: [{ required: true, message: '请输入路由路径', trigger: 'blur' }],
}

async function fetchMenus() {
  loading.value = true
  try {
    const res = await getMenuList()
    menuList.value = res.data
    menuTreeOptions.value = [{ id: null, title: '顶级菜单', children: res.data }]
  } finally {
    loading.value = false
  }
}

function resetForm() {
  form.id = null
  form.parent_id = null
  form.name = ''
  form.path = ''
  form.component = ''
  form.icon = ''
  form.title = ''
  form.sort_order = 0
  form.menu_type = 'menu'
  form.permission = ''
  form.is_hidden = false
}

function handleAdd() {
  resetForm()
  isEdit.value = false
  dialogTitle.value = '新增菜单'
  dialogVisible.value = true
}

function handleAddChild(row) {
  resetForm()
  form.parent_id = row.id
  isEdit.value = false
  dialogTitle.value = '新增子菜单'
  dialogVisible.value = true
}

function handleEdit(row) {
  isEdit.value = true
  dialogTitle.value = '编辑菜单'
  Object.assign(form, row)
  dialogVisible.value = true
}

async function handleSubmit() {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  submitLoading.value = true
  try {
    if (isEdit.value) {
      await updateMenu(form.id, form)
      ElMessage.success('更新成功')
    } else {
      await createMenu(form)
      ElMessage.success('创建成功')
    }
    dialogVisible.value = false
    fetchMenus()
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || '操作失败')
  } finally {
    submitLoading.value = false
  }
}

function handleDelete(row) {
  ElMessageBox.confirm(`确定删除菜单 "${row.title}" 吗？`, '提示', {
    confirmButtonText: '确定',
    cancelButtonText: '取消',
    type: 'warning',
  }).then(async () => {
    await deleteMenu(row.id)
    ElMessage.success('删除成功')
    fetchMenus()
  })
}

onMounted(fetchMenus)
</script>

<style scoped lang="scss">
.page-container {
  padding: 20px;
  .toolbar {
    margin-bottom: 16px;
  }
}
</style>
